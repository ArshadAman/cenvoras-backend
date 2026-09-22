import re
import logging
from decimal import Decimal
from django.db import transaction
from django.conf import settings

logger = logging.getLogger(__name__)


def get_tenant_full_prefix(tenant, document_type='sales_invoice', prefix=None):
    """
    Standardize the document sequence prefix per tenant.
    Ensures upper-case formatting and incorporates tenant code where applicable.
    """
    tenant_code = str(tenant.id)[:4].upper()

    if document_type == 'sales_invoice':
        base = (prefix or getattr(tenant, 'invoice_prefix', 'INV-') or 'INV-').strip().upper()
        if not base.endswith('-'):
            base = f"{base}-"
        if tenant_code in base:
            return base
        return f"{base}{tenant_code}-"

    elif document_type == 'quotation':
        base = (prefix or 'QT-').strip().upper()
        if not base.endswith('-'):
            base = f"{base}-"
        if tenant_code in base:
            return base
        return f"{base}{tenant_code}-"

    elif document_type == 'credit_note':
        base = (prefix or 'CN-').strip().upper()
        if not base.endswith('-'):
            base = f"{base}-"
        return base

    elif document_type == 'debit_note':
        base = (prefix or 'DN-').strip().upper()
        if not base.endswith('-'):
            base = f"{base}-"
        return base

    elif document_type == 'delivery_challan':
        base = (prefix or 'DC-').strip().upper()
        if not base.endswith('-'):
            base = f"{base}-"
        if tenant_code in base:
            return base
        return f"{base}{tenant_code}-"

    # Fallback
    base = (prefix or 'DOC-').strip().upper()
    if not base.endswith('-'):
        base = f"{base}-"
    return base


def is_auto_sequence_number(full_prefix, number_str):
    """
    Check if a given number string follows the standard auto-generated sequence pattern:
    <full_prefix><digits>
    """
    if not number_str or not isinstance(number_str, str):
        return False
    if not number_str.startswith(full_prefix):
        return False
    suffix = number_str[len(full_prefix):]
    return suffix.isdigit()


def _get_model_and_field(document_type):
    from billing.models import SalesInvoice
    from billing.models_returns import CreditNote, DebitNote
    from billing.models_sidecar import Quotation, SalesOrder, DeliveryChallan

    mapping = {
        'sales_invoice': (SalesInvoice, 'invoice_number'),
        'quotation': (Quotation, 'quotation_number'),
        'credit_note': (CreditNote, 'credit_note_number'),
        'debit_note': (DebitNote, 'debit_note_number'),
        'sales_order': (SalesOrder, 'order_number'),
        'delivery_challan': (DeliveryChallan, 'challan_number'),
    }
    return mapping.get(document_type, (SalesInvoice, 'invoice_number'))


def _number_exists(tenant, document_type, candidate_number):
    model, field_name = _get_model_and_field(document_type)
    filter_kwargs = {
        'created_by': tenant,
        field_name: candidate_number,
    }
    return model.objects.filter(**filter_kwargs).exists()


def _get_existing_max_number(tenant, document_type, full_prefix):
    """
    Efficiently scan only the string column for existing records matching prefix
    without loading Django model instances into Python memory.
    """
    model, field_name = _get_model_and_field(document_type)
    filter_kwargs = {
        'created_by': tenant,
        f"{field_name}__startswith": full_prefix,
    }
    existing_numbers = model.objects.filter(**filter_kwargs).values_list(field_name, flat=True)
    
    max_num = 0
    for num_str in existing_numbers:
        suffix = str(num_str)[len(full_prefix):]
        try:
            val = int(suffix)
            if val > max_num:
                max_num = val
        except (ValueError, TypeError):
            continue
    return max_num


def allocate_next_number(tenant, document_type='sales_invoice', prefix=None, min_digits=3, max_retries=5):
    """
    Atomically allocate the next guaranteed unique document number.
    Uses SELECT FOR UPDATE on InvoiceSequence to eliminate multi-user race conditions.
    Includes retry logic with backoff to withstand concurrent transactions.
    """
    import time
    from django.db import OperationalError
    from billing.models import InvoiceSequence

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix)

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                seq, created = InvoiceSequence.objects.select_for_update().get_or_create(
                    tenant=tenant,
                    document_type=document_type,
                    prefix=full_prefix,
                    defaults={'last_number': 0}
                )

                if created or seq.last_number == 0:
                    max_existing = _get_existing_max_number(tenant, document_type, full_prefix)
                    seq.last_number = max_existing

                seq.last_number += 1

                # Guard against any manual out-of-order records inserted in the past
                while _number_exists(tenant, document_type, f"{full_prefix}{seq.last_number:0{min_digits}d}"):
                    seq.last_number += 1

                seq.save(update_fields=['last_number', 'updated_at'])
                allocated = f"{full_prefix}{seq.last_number:0{min_digits}d}"
                logger.debug(f"Allocated {document_type} number {allocated} for tenant {tenant.id}")
                return allocated
        except OperationalError:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.05 * (2 ** attempt))


def preview_next_number(tenant, document_type='sales_invoice', prefix=None, min_digits=3):
    """
    Preview the next anticipated document number for UI forms without locking the database table.
    """
    from billing.models import InvoiceSequence

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix)

    seq = InvoiceSequence.objects.filter(
        tenant=tenant,
        document_type=document_type,
        prefix=full_prefix,
    ).first()

    current_num = seq.last_number if seq else 0
    if not seq or current_num == 0:
        current_num = _get_existing_max_number(tenant, document_type, full_prefix)

    candidate = current_num + 1
    while _number_exists(tenant, document_type, f"{full_prefix}{candidate:0{min_digits}d}"):
        candidate += 1

    formatted = f"{full_prefix}{candidate:0{min_digits}d}"
    suffix = f"{candidate:0{min_digits}d}"
    return formatted, suffix


def sync_sequence_after_creation(tenant, document_type, prefix, actual_number):
    """
    Ensure the sequence table tracks manual entries so subsequent allocations don't collide.
    """
    from billing.models import InvoiceSequence

    if not actual_number or not isinstance(actual_number, str):
        return

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix)
    if not is_auto_sequence_number(full_prefix, actual_number):
        return

    try:
        suffix_int = int(actual_number[len(full_prefix):])
    except (ValueError, TypeError):
        return

    with transaction.atomic():
        seq, _ = InvoiceSequence.objects.select_for_update().get_or_create(
            tenant=tenant,
            document_type=document_type,
            prefix=full_prefix,
            defaults={'last_number': suffix_int}
        )
        if suffix_int > seq.last_number:
            seq.last_number = suffix_int
            seq.save(update_fields=['last_number', 'updated_at'])

import re
import logging
from django.db import transaction

logger = logging.getLogger(__name__)

DRAFT_NUMBER_PREFIXES = (
    'DFT-',
    'D-QT-',
    'D-DC-',
    'D-SO-',
    'D-BILL-',
    'D-CN-',
    'D-DN-',
    'PB-DRAFT-',
)

DOCUMENT_LABELS = {
    'sales_invoice': 'Invoice',
    'quotation': 'Quotation',
    'delivery_challan': 'Challan',
    'sales_order': 'Sales order',
    'purchase_order': 'Purchase order',
    'purchase_bill': 'Bill',
    'credit_note': 'Credit note',
    'debit_note': 'Debit note',
}


class DuplicateDocumentNumber(Exception):
    def __init__(self, number, document_type='sales_invoice'):
        self.number = number
        self.document_type = document_type
        self.label = DOCUMENT_LABELS.get(document_type, 'Document')
        super().__init__(f'{self.label} number {number} already exists')


def _ensure_trailing_separator(base):
    raw = (base or '').strip().upper()
    if not raw:
        return raw
    if raw.endswith('-') or raw.endswith('/'):
        return raw
    return f"{raw}-"


def get_tenant_full_prefix(tenant, document_type='sales_invoice', prefix=None, is_draft=False):
    """
    Standardize the document sequence prefix per tenant.
    Draft and confirmed documents share one live prefix; is_draft is ignored.
    """
    if document_type == 'sales_invoice':
        base = prefix or getattr(tenant, 'invoice_prefix', 'INV') or 'INV'
    elif document_type == 'quotation':
        base = prefix or getattr(tenant, 'quotation_prefix', 'QT') or 'QT'
    elif document_type == 'credit_note':
        base = prefix or 'CN'
    elif document_type == 'debit_note':
        base = prefix or 'DN'
    elif document_type == 'delivery_challan':
        base = prefix or getattr(tenant, 'delivery_challan_prefix', 'DC') or 'DC'
    elif document_type == 'sales_order':
        base = prefix or 'SO'
    elif document_type == 'purchase_bill':
        base = prefix or 'BILL'
    elif document_type == 'purchase_order':
        base = prefix or 'PO'
    else:
        base = prefix or 'DOC'

    return _ensure_trailing_separator(base)


def display_document_number(number_str):
    """
    Strip legacy 4-character tenant UUID middle code from document numbers
    for clean customer-facing display.
    Example: 'INV-7E4A-002' → 'INV-002', 'QT-ABBA-001' → 'QT-001'
    Idempotent: 'INV-002' → 'INV-002' (no change if already clean).
    """
    if not number_str or not isinstance(number_str, str):
        return number_str or ''
    pattern = r'^([A-Za-z]+-)[0-9A-Fa-f]{4}-(\d+)$'
    match = re.match(pattern, number_str.strip())
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return number_str


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


def is_placeholder_number(number_str):
    if not number_str or not str(number_str).strip():
        return True
    s = str(number_str).strip()
    return s.startswith(DRAFT_NUMBER_PREFIXES)


def _get_model_and_field(document_type):
    from billing.models import SalesInvoice, PurchaseBill, PurchaseOrder
    from billing.models_returns import CreditNote, DebitNote
    from billing.models_sidecar import Quotation, SalesOrder, DeliveryChallan

    mapping = {
        'sales_invoice': (SalesInvoice, 'invoice_number'),
        'quotation': (Quotation, 'quotation_number'),
        'credit_note': (CreditNote, 'credit_note_number'),
        'debit_note': (DebitNote, 'debit_note_number'),
        'sales_order': (SalesOrder, 'order_number'),
        'delivery_challan': (DeliveryChallan, 'challan_number'),
        'purchase_bill': (PurchaseBill, 'bill_number'),
        'purchase_order': (PurchaseOrder, 'po_number'),
    }
    return mapping.get(document_type, (SalesInvoice, 'invoice_number'))


def _number_exists(tenant, document_type, candidate_number):
    model, field_name = _get_model_and_field(document_type)
    filter_kwargs = {
        'created_by': tenant,
        field_name: candidate_number,
    }
    return model.objects.filter(**filter_kwargs).exists()


def number_taken(tenant, document_type, number, exclude_pk=None):
    if not number:
        return False
    model, field_name = _get_model_and_field(document_type)
    qs = model.objects.filter(created_by=tenant, **{field_name: number})
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _get_existing_max_number(tenant, document_type, full_prefix):
    """
    Highest numeric suffix currently allocated for this document type,
    across draft and confirmed rows and every prefix.
    """
    model, field_name = _get_model_and_field(document_type)
    existing_numbers = model.objects.filter(created_by=tenant).values_list(field_name, flat=True)

    max_num = 0
    for num_str in existing_numbers:
        if not num_str:
            continue
        s = str(num_str).strip()
        if full_prefix and s.startswith(full_prefix):
            raw_suffix = s[len(full_prefix):].lstrip('-/_ ')
            if raw_suffix.isdigit():
                val = int(raw_suffix)
                if val > max_num:
                    max_num = val
                continue
        match = re.search(r'(\d+)$', s)
        if match:
            try:
                val = int(match.group(1))
                if val > max_num:
                    max_num = val
            except (ValueError, TypeError):
                continue
    return max_num


def allocate_next_number(tenant, document_type='sales_invoice', prefix=None, min_digits=3, max_retries=5, is_draft=False):
    """
    Atomically allocate the next guaranteed unique document number.
    Draft and confirmed share one live sequence: max(allocated suffix) + 1.
    is_draft is ignored so new drafts receive the live prefix.
    """
    import time
    from django.db import OperationalError
    from billing.models import InvoiceSequence

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                seq, created = InvoiceSequence.objects.select_for_update().get_or_create(
                    tenant=tenant,
                    document_type=document_type,
                    prefix=full_prefix,
                    defaults={'last_number': 0}
                )

                max_existing = _get_existing_max_number(tenant, document_type, full_prefix)
                current_base = max(seq.last_number, max_existing)
                seq.last_number = current_base + 1

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


def preview_next_number(tenant, document_type='sales_invoice', prefix=None, min_digits=3, is_draft=False):
    """
    Preview the next anticipated document number for UI forms without locking the database table.
    """
    from billing.models import InvoiceSequence

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)

    seq = InvoiceSequence.objects.filter(
        tenant=tenant,
        document_type=document_type,
        prefix=full_prefix,
    ).first()

    current_num = seq.last_number if seq else 0
    max_existing = _get_existing_max_number(tenant, document_type, full_prefix)
    current_base = max(current_num, max_existing)

    candidate = current_base + 1
    while _number_exists(tenant, document_type, f"{full_prefix}{candidate:0{min_digits}d}"):
        candidate += 1

    formatted = f"{full_prefix}{candidate:0{min_digits}d}"
    suffix = f"{candidate:0{min_digits}d}"
    return formatted, suffix


def keep_or_allocate_number(
    tenant,
    document_type,
    number,
    *,
    exclude_pk=None,
    prefix=None,
    on_collision='allocate',
):
    """
    Keep a live unique number. Allocate max+1 if blank, draft-prefixed, or colliding.
    on_collision='conflict' raises DuplicateDocumentNumber for a taken custom number.
    """
    if number and not is_placeholder_number(number):
        if not number_taken(tenant, document_type, number, exclude_pk=exclude_pk):
            return str(number).strip()
        full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)
        if on_collision == 'conflict' and not is_auto_sequence_number(full_prefix, number):
            raise DuplicateDocumentNumber(number, document_type)
        # Stolen auto-number or promotion after another doc took it: next shared max+1
    return allocate_next_number(tenant, document_type, prefix=prefix, is_draft=False)


def sync_sequence_after_creation(tenant, document_type, prefix, actual_number):
    """
    Ensure the sequence table tracks manual entries so subsequent allocations don't collide.
    Never advances canonical sequence for leftover draft-prefixed numbers.
    """
    from billing.models import InvoiceSequence

    if not actual_number or not isinstance(actual_number, str):
        return

    if is_placeholder_number(actual_number):
        return

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)
    if not is_auto_sequence_number(full_prefix, actual_number):
        return

    match = re.search(r'(\d+)$', actual_number)
    if not match:
        return

    try:
        suffix_int = int(match.group(1))
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

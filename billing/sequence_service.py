import re
import logging
import uuid
from django.db import transaction

logger = logging.getLogger(__name__)

DRAFT_NUMBER_PREFIXES = (
    'DFT-',
    'D-QT-',
    'D-DC-',
    'D-SO-',
    'D-PO-',
    'D-BILL-',
    'D-CN-',
    'D-DN-',
    'PB-DRAFT-',
)

UNCONFIRMED_STATUSES = frozenset({'draft', 'cancelled'})

DRAFT_PLACEHOLDER_BY_TYPE = {
    'sales_invoice': 'DFT-',
    'quotation': 'D-QT-',
    'delivery_challan': 'D-DC-',
    'sales_order': 'D-SO-',
    'purchase_order': 'D-PO-',
    'purchase_bill': 'D-BILL-',
    'credit_note': 'D-CN-',
    'debit_note': 'D-DN-',
}

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
        base = prefix or getattr(tenant, 'sales_order_prefix', 'SO') or 'SO'
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
    if s.startswith(DRAFT_NUMBER_PREFIXES):
        return True
    return s.upper().endswith('-AUTO') or s.upper().endswith('/AUTO')


def status_should_allocate(status):
    """Blank numbers are allocated only for confirmed (non-draft) documents."""
    if status is None:
        return True
    return str(status).strip().lower() not in UNCONFIRMED_STATUSES


def document_status_from(validated_data, serializer=None, default=None):
    if validated_data and validated_data.get('status') is not None:
        return validated_data.get('status')
    if serializer is not None:
        request = (serializer.context or {}).get('request')
        if request is not None:
            raw = (getattr(request, 'data', {}) or {}).get('status')
            if raw is not None:
                return raw
        if serializer.instance is not None:
            return getattr(serializer.instance, 'status', default)
    return default


def make_draft_placeholder(document_type='sales_invoice'):
    """Unique non-live number so drafts do not collide on unique(created_by, number)."""
    prefix = DRAFT_PLACEHOLDER_BY_TYPE.get(document_type, 'DFT-')
    return f"{prefix}X{uuid.uuid4().hex[:8].upper()}"


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


def _numeric_suffix(number_str, full_prefix):
    """
    Suffix from a live or draft auto-number only.
    Custom numbers such as CUSTOM-9 must not advance INV/QT/DC sequences.
    """
    if not number_str:
        return None
    s = str(number_str).strip()
    if not s:
        return None

    def digits_after(prefix):
        if not prefix or not s.startswith(prefix):
            return None
        rest = s[len(prefix):].lstrip('-/_ ')
        uuid_mid = re.match(r'^[0-9A-Fa-f]{4}[-/_](\d+)$', rest)
        if uuid_mid:
            return int(uuid_mid.group(1))
        if rest.isdigit():
            return int(rest)
        return None

    # Live auto-numbers only. Draft prefixes and custom numbers must not
    # advance INV/QT/DC sequences.
    return digits_after(full_prefix)


def _get_existing_max_number(tenant, document_type, full_prefix):
    """
    Highest numeric suffix currently allocated for this document type,
    across draft and confirmed auto-numbers.
    """
    model, field_name = _get_model_and_field(document_type)
    existing_numbers = model.objects.filter(created_by=tenant).values_list(field_name, flat=True)

    max_num = 0
    for num_str in existing_numbers:
        val = _numeric_suffix(num_str, full_prefix)
        if val is not None and val > max_num:
            max_num = val
    return max_num


def allocate_next_number(tenant, document_type='sales_invoice', prefix=None, min_digits=3, max_retries=5, is_draft=False):
    """
    Atomically allocate the next guaranteed unique document number.
    Next value is the highest remaining live suffix + 1 (sticky last_number
    cannot invent a gap after deletes). Call inside the same transaction as
    the document insert so a failed create cannot leave a reserved hole.
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
                seq.last_number = max_existing + 1

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
    Preview the next anticipated document number for UI forms without locking
    or incrementing InvoiceSequence.last_number.
    """
    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)

    max_existing = _get_existing_max_number(tenant, document_type, full_prefix)
    candidate = max_existing + 1
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
    allocate_if_empty=True,
):
    """
    Keep a live unique number. Allocate max+1 if blank, draft-prefixed, or colliding.
    on_collision='conflict' raises DuplicateDocumentNumber for a taken custom number.
    allocate_if_empty=False leaves drafts unnumbered (caller may assign a placeholder).
    """
    if number and not is_placeholder_number(number):
        if not number_taken(tenant, document_type, number, exclude_pk=exclude_pk):
            return str(number).strip()
        full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)
        if on_collision == 'conflict' and not is_auto_sequence_number(full_prefix, number):
            raise DuplicateDocumentNumber(number, document_type)
        # Stolen auto-number or promotion after another doc took it: next shared max+1
    if not allocate_if_empty:
        if number and is_placeholder_number(number) and str(number).strip():
            return str(number).strip()
        return ''
    return allocate_next_number(tenant, document_type, prefix=prefix, is_draft=False)


def assign_document_number(
    tenant,
    document_type,
    number,
    *,
    status=None,
    exclude_pk=None,
    prefix=None,
    on_collision='allocate',
    require_placeholder=False,
):
    """
    Keep a custom number. Allocate a live sequence number only when confirmed.
    Drafts stay blank, or get a unique DFT-/D-* placeholder when the table
    cannot store two empty numbers.
    """
    allocate = status_should_allocate(status)
    assigned = keep_or_allocate_number(
        tenant,
        document_type,
        number,
        exclude_pk=exclude_pk,
        prefix=prefix,
        on_collision=on_collision,
        allocate_if_empty=allocate,
    )
    if not allocate and not assigned and require_placeholder:
        return make_draft_placeholder(document_type)
    return assigned


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


def reconcile_sequence_from_db(tenant, document_type, prefix=None):
    """
    After a document is deleted, drop last_number to the highest remaining suffix
    so the next allocate reuses the gap at the end (not middle gaps).
    """
    from billing.models import InvoiceSequence

    if not tenant:
        return

    full_prefix = get_tenant_full_prefix(tenant, document_type, prefix, is_draft=False)
    max_existing = _get_existing_max_number(tenant, document_type, full_prefix)

    with transaction.atomic():
        seq = InvoiceSequence.objects.select_for_update().filter(
            tenant=tenant,
            document_type=document_type,
            prefix=full_prefix,
        ).first()
        if seq and seq.last_number > max_existing:
            seq.last_number = max_existing
            seq.save(update_fields=['last_number', 'updated_at'])

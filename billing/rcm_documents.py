"""
RCM self-invoice (s.31(3)(f), Rule 47A) and payment voucher (s.31(3)(g)). See models_rcm.py.

Rule 47A's 30 days run from PurchaseBill.rcm_receipt_date ('Date of receipt of goods/services',
blank = bill date). The payment voucher is created and dated from each payment entry to the
supplier (vendor_payment_create), not from the bill.

Time of supply under reverse charge (Rudra, #219) - used to place RCM tax in GSTR-3B 3.1(d):
  goods    s.12(3): earliest of receipt date, payment date, supplier invoice date + 31 days
  services s.13(3): earlier of payment date, supplier invoice date + 61 days
"Payment date" = the earliest RCM payment voucher for the bill (i.e. the first payment entry).
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from billing.itc import bill_tax_classification, line_is_rcm, vendor_registered
from billing.models_rcm import RcmPaymentVoucher, RcmSelfInvoice

ZERO = Decimal('0.00')
SELF_INVOICE_DAYS = 30


class RcmDocumentError(ValueError):
    pass


def _fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def _next_number(model, tenant, prefix, on_date):
    """Own series per tenant and FY: e.g. RCM-2026-0001 (<= 16 characters, Rule 46)."""
    base = f"{prefix}-{_fy_start_year(on_date)}-"
    used = set(model.objects.filter(created_by=tenant, number__startswith=base).values_list('number', flat=True))
    n = len(used) + 1
    while f"{base}{n:04d}" in used:
        n += 1
    return f"{base}{n:04d}"


def receipt_date(bill):
    return getattr(bill, 'rcm_receipt_date', None) or bill.bill_date


def first_payment_date(bill):
    return (RcmPaymentVoucher.objects.filter(purchase_bill=bill).order_by('date')
            .values_list('date', flat=True).first())


def rcm_time_of_supply(bill, is_service, payment_date=None):
    """Time of supply of an RCM supply on this bill (goods s.12(3) / services s.13(3))."""
    pay = payment_date if payment_date is not None else first_payment_date(bill)
    if is_service:
        candidates = [bill.bill_date + timedelta(days=61)]
    else:
        candidates = [receipt_date(bill), bill.bill_date + timedelta(days=31)]
    if pay:
        candidates.append(pay)
    return min(candidates)


def bill_has_rcm(bill):
    return any(line_is_rcm(bill, it) for it in bill.items.all())


def needs_self_invoice(bill):
    """RCM supply from an unregistered supplier."""
    return bill_has_rcm(bill) and not vendor_registered(bill)


def self_invoice_deadline(receipt_date):
    return receipt_date + timedelta(days=SELF_INVOICE_DAYS)


def _rcm_amounts(bill):
    c = bill_tax_classification(bill)
    return c['rcm_taxable'], c['rcm_payable']


@transaction.atomic
def create_self_invoice(tenant, bill, on_date):
    if bill.created_by_id != tenant.id:
        raise RcmDocumentError('Purchase bill not found.')
    if not bill_has_rcm(bill):
        raise RcmDocumentError('This bill has no reverse-charge lines, so no self-invoice is needed.')
    if vendor_registered(bill):
        raise RcmDocumentError('A self-invoice (s.31(3)(f)) is only for supplies from an unregistered supplier. '
                               'This supplier has a GSTIN: use their tax invoice.')
    if RcmSelfInvoice.objects.filter(purchase_bill=bill).exists():
        raise RcmDocumentError('A self-invoice already exists for this bill.')
    received = receipt_date(bill)
    if on_date < received:
        raise RcmDocumentError('Self-invoice date cannot be before the date the supply was received.')
    deadline = self_invoice_deadline(received)
    if on_date > deadline:
        raise RcmDocumentError(f'Rule 47A: the self-invoice must be issued within {SELF_INVOICE_DAYS} days of '
                               f'receiving the supply (by {deadline:%d-%m-%Y}).')
    taxable, tax = _rcm_amounts(bill)
    vendor = getattr(bill, 'vendor', None)
    return RcmSelfInvoice.objects.create(
        created_by=tenant, purchase_bill=bill, number=_next_number(RcmSelfInvoice, tenant, 'RCM', on_date),
        date=on_date, receipt_date=received, supplier_name=bill.vendor_name,
        supplier_address=(bill.vendor_address or getattr(vendor, 'address', '') or ''),
        supplier_state=(getattr(vendor, 'state', '') or ''), place_of_supply=(bill.place_of_supply or ''),
        taxable_value=taxable, cgst=tax['cgst'], sgst=tax['sgst'], igst=tax['igst'])


def create_payment_voucher(tenant, bill, on_date, amount_paid, mode='', reference=''):
    """s.31(3)(g): one voucher per payment entry to the supplier of an RCM supply.
    Tax shown is the RCM tax attributable to this payment (pro rata to the amount payable to
    the supplier; the payment that clears the bill takes the remainder). Returns None when the
    bill has no RCM lines. Call inside the payment's transaction."""
    if bill.created_by_id != tenant.id or not bill_has_rcm(bill):
        return None
    amount_paid = Decimal(str(amount_paid or 0)).quantize(Decimal('0.01'))
    if amount_paid <= 0:
        raise RcmDocumentError('Amount paid must be more than 0.')
    c = bill_tax_classification(bill)
    payable = c['vendor_payable']
    prev = list(RcmPaymentVoucher.objects.filter(purchase_bill=bill))
    paid_before = sum((v.amount_paid for v in prev), ZERO)
    clears = payable <= 0 or paid_before + amount_paid >= payable
    parts = {}
    for field, total in (('taxable_value', c['rcm_taxable']), ('cgst', c['rcm_payable']['cgst']),
                         ('sgst', c['rcm_payable']['sgst']), ('igst', c['rcm_payable']['igst'])):
        done = sum((getattr(v, field) for v in prev), ZERO)
        parts[field] = (max(total - done, ZERO) if clears
                        else (total * amount_paid / payable).quantize(Decimal('0.01')))
    return RcmPaymentVoucher.objects.create(
        created_by=tenant, purchase_bill=bill, number=_next_number(RcmPaymentVoucher, tenant, 'RPV', on_date),
        date=on_date, supplier_name=bill.vendor_name, amount_paid=amount_paid, payment_mode=mode or '',
        payment_reference=reference or '', **parts)


def self_invoices_due(tenant, today):
    """RCM bills from unregistered suppliers with no self-invoice yet, with deadline/overdue."""
    from billing.models import PurchaseBill
    out = []
    qs = (PurchaseBill.objects.filter(created_by=tenant, rcm_self_invoice__isnull=True)
          .select_related('vendor').prefetch_related('items').order_by('bill_date'))
    for bill in qs:
        if not needs_self_invoice(bill):
            continue
        deadline = self_invoice_deadline(receipt_date(bill))
        out.append({'purchase_bill': str(bill.id), 'bill_number': bill.bill_number,
                    'bill_date': bill.bill_date.isoformat(), 'supplier_name': bill.vendor_name,
                    'deadline': deadline.isoformat(), 'overdue': today > deadline})
    return out

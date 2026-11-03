"""
UAT P1 items 6 and 7 (Rudra CA): input tax credit and reverse charge on purchases.

Item 6 - ITC = tax only on bills where the vendor's GSTIN is valid and the credit isn't
blocked under s.17(5). Bills from unregistered vendors add 0 to GSTR-3B 4A(5). The only
exception is RCM (item 7), whose ITC is reported in 4A(3).

Item 7 - an unregistered vendor can't charge GST, so line tax is locked at 0 unless the
line is flagged "RCM applicable" (any vendor, e.g. GTA or advocates). An RCM line is taxed
at the notified rate entered on the line and posts
    Dr Input tax (RCM)  /  Cr RCM tax payable
while the vendor is owed the taxable value only. RCM tax goes to GSTR-3B 3.1(d) and its
ITC to 4A(3) (when not blocked).

Limits of the data we hold (listed for Rudra): Cenvora does not store a vendor's GST
registration/cancellation dates, so "valid on the bill date" is checked as "a valid GSTIN
is on the bill (or the vendor) today". A legacy bill-level `is_reverse_charge` flag is
treated as "every line is RCM".
"""
from decimal import Decimal

ZERO = Decimal('0.00')


def bill_vendor_gstin(bill):
    gstin = (getattr(bill, 'vendor_gstin', None) or '').strip()
    if not gstin and getattr(bill, 'vendor', None) is not None:
        gstin = (bill.vendor.gstin or '').strip()
    return gstin.upper()


def vendor_registered(bill):
    """True when the bill carries a valid supplier GSTIN."""
    from billing.gst_utils import validate_gstin_format
    gstin = bill_vendor_gstin(bill)
    if not gstin:
        return False
    try:
        return bool(validate_gstin_format(gstin, required=True))
    except ValueError:
        return False


def line_is_rcm(bill, item):
    return bool(getattr(item, 'is_rcm', False) or getattr(bill, 'is_reverse_charge', False))


def lock_unregistered_line_tax(vendor_gstin, is_reverse_charge, items):
    """
    Serializer helper (item 7): for an unregistered vendor, force tax to 0 on every non-RCM
    line. `items` are validated item dicts; modified in place. Returns True if any tax was zeroed.
    """
    from billing.gst_utils import validate_gstin_format
    try:
        registered = bool(validate_gstin_format(vendor_gstin or '', required=True))
    except ValueError:
        registered = False
    if registered:
        return False
    changed = False
    for it in items or []:
        if is_reverse_charge or it.get('is_rcm'):
            continue
        if Decimal(str(it.get('tax', 0) or 0)) != 0:
            it['tax'] = Decimal('0.00')
            changed = True
    return changed


def split_tax(tax, is_interstate):
    """(cgst, sgst, igst) for a tax amount."""
    tax = Decimal(str(tax or 0))
    if is_interstate:
        return ZERO, ZERO, tax
    cgst = (tax / Decimal('2')).quantize(Decimal('0.01'))
    return cgst, tax - cgst, ZERO


def bill_tax_classification(bill, buyer_state_code=None):
    """
    Per-bill buckets (Decimal) used by the ledger, GSTR-3B and the dashboard:
      taxable, vendor_payable,
      itc_normal {cgst,sgst,igst}   -> 4A(5)  (registered vendor, not blocked, not RCM)
      itc_rcm    {cgst,sgst,igst}   -> 4A(3)  (RCM, not blocked)
      rcm_payable{cgst,sgst,igst}   -> 3.1(d) (all RCM tax)
      blocked_tax                   -> added to cost (s.17(5)), never ITC
      ineligible_tax                -> tax on a non-RCM line of an unregistered vendor (legacy data); not ITC
    """
    from billing.gst_utils import calculate_item_tax_breakup, get_seller_state_code
    from billing.gst_rules import is_purchase_interstate
    if buyer_state_code is None:
        tenant = getattr(bill.created_by, 'active_tenant', bill.created_by)
        buyer_state_code = get_seller_state_code(tenant)
    inter = is_purchase_interstate(bill, buyer_state_code)
    registered = vendor_registered(bill)

    def _z():
        return {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO}

    out = {'taxable': ZERO, 'vendor_payable': ZERO, 'itc_normal': _z(), 'itc_rcm': _z(),
           'rcm_payable': _z(), 'rcm_taxable': ZERO, 'blocked_tax': ZERO, 'ineligible_tax': ZERO,
           'is_interstate': inter, 'vendor_registered': registered}
    for item in bill.items.all():
        taxable, tax, *_ = calculate_item_tax_breakup(item, is_interstate=False)
        c, s, i = split_tax(tax, inter)
        out['taxable'] += taxable
        blocked = bool(getattr(item, 'itc_blocked', False))
        if line_is_rcm(bill, item):
            out['vendor_payable'] += taxable
            out['rcm_taxable'] += taxable
            for k, v in (('cgst', c), ('sgst', s), ('igst', i)):
                out['rcm_payable'][k] += v
                if not blocked:
                    out['itc_rcm'][k] += v
            if blocked:
                out['blocked_tax'] += tax
            continue
        out['vendor_payable'] += taxable + tax
        if blocked:
            out['blocked_tax'] += tax
        elif not registered:
            out['ineligible_tax'] += tax
        else:
            for k, v in (('cgst', c), ('sgst', s), ('igst', i)):
                out['itc_normal'][k] += v
    return out


def itc_summary(tenant, date_from, date_to):
    """Sum of bill_tax_classification over final purchase bills dated in [from, to]."""
    from billing.gst_utils import get_seller_state_code
    from billing.models import PurchaseBill
    buyer = get_seller_state_code(tenant)
    tot = {'itc_normal': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'itc_rcm': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'rcm_payable': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'rcm_taxable': ZERO, 'blocked_tax': ZERO, 'ineligible_tax': ZERO}
    # PurchaseBill has no draft/cancelled status in Cenvora; every saved bill counts.
    qs = PurchaseBill.objects.filter(created_by=tenant, bill_date__gte=date_from, bill_date__lte=date_to)
    for bill in qs.select_related('vendor', 'created_by').prefetch_related('items'):
        b = bill_tax_classification(bill, buyer)
        for bucket in ('itc_normal', 'itc_rcm', 'rcm_payable'):
            for k in ('cgst', 'sgst', 'igst'):
                tot[bucket][k] += b[bucket][k]
        for k in ('rcm_taxable', 'blocked_tax', 'ineligible_tax'):
            tot[k] += b[k]
    return tot

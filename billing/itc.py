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
    """True when the bill carries a supplier GSTIN in valid format. A saved GSTIN that fails the
    check character stays accepted (#212: flagged 'Invalid GSTIN, please correct', not rejected);
    new or changed GSTINs are fully checked by the party/bill serializers."""
    from billing.gst_utils import validate_gstin_format
    gstin = bill_vendor_gstin(bill)
    if not gstin:
        return False
    try:
        return bool(validate_gstin_format(gstin, required=True, legacy=gstin))
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
        registered = bool(validate_gstin_format(vendor_gstin or '', required=True, legacy=vendor_gstin))
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


def s16_4_deadline(bill_date):
    """s.16(4): ITC on an invoice can't be taken after 30 November following the end of the FY
    of the invoice (the earlier 'annual return' limb is not modelled)."""
    from datetime import date
    fy_end_year = bill_date.year + 1 if bill_date.month >= 4 else bill_date.year
    return date(fy_end_year, 11, 30)


def itc_time_barred(bill):
    """True when the bill was recorded in Cenvora after its s.16(4) deadline.

    VERIFY: should be based on the 3B period in which ITC is claimed (by 30 Nov after the FY,
    or the annual return date if earlier). The entry date is a proxy until Cenvora records the
    claim period."""
    created = getattr(bill, 'created_at', None)
    if not created or not getattr(bill, 'bill_date', None):
        return False
    from django.utils import timezone
    recorded = timezone.localtime(created).date() if timezone.is_aware(created) else created.date()
    return recorded > s16_4_deadline(bill.bill_date)


def itc_pos_lost(bill, buyer_state_code):
    """True when the bill's stated place of supply is a state other than the buyer's
    registration state (e.g. a hotel in another state charging that state's CGST/SGST):
    the buyer can't take that ITC (PoS rules). Only an explicit place_of_supply counts."""
    from billing.gst_utils import get_pos_state_code
    pos = (getattr(bill, 'place_of_supply', None) or '').strip()
    if not pos or not buyer_state_code:
        return False
    code = get_pos_state_code(pos, fallback_state=None)
    return bool(code) and str(code).strip() != str(buyer_state_code).strip()


def bill_tax_classification(bill, buyer_state_code=None, only=None):
    """
    Per-bill buckets (Decimal) used by the ledger, GSTR-3B and the dashboard:
      taxable, vendor_payable,
      itc_normal {cgst,sgst,igst}   -> 4A(5)  (registered vendor, not blocked, not RCM)
      itc_rcm    {cgst,sgst,igst}   -> 4A(3)  (RCM, not blocked)
      rcm_payable{cgst,sgst,igst}   -> 3.1(d) (all RCM tax)
      blocked_tax                   -> added to cost (s.17(5)), never ITC
      ineligible_tax                -> tax on a non-RCM line of an unregistered vendor (legacy data); not ITC

    GSTR-3B view (Rudra, #214):
      itc_4a_normal / itc_4a_rcm {..} -> 4A(5) / 4A(3) GROSS, i.e. including s.17(5) blocked ITC
      blocked_split {..}              -> 4B(1) reversal of the blocked part
      lost_4d2 {..}, lost_tax         -> 4D(2): ITC lost to PoS rules or the s.16(4) time limit
                                         (never in 4A; added to cost in the books)
    itc_normal / itc_rcm stay NET claimable (= 4A - 4B(1)); the ledger posts those.

    only: None (all lines), 'rcm' (reverse-charge lines only) or 'non_rcm'. GSTR-3B places a
    bill's RCM lines by their time of supply (#219) and the rest by bill date.
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
           'is_interstate': inter, 'vendor_registered': registered,
           'itc_4a_normal': _z(), 'itc_4a_rcm': _z(), 'blocked_split': _z(), 'lost_4d2': _z(),
           'lost_tax': ZERO}
    lost = registered and (itc_pos_lost(bill, buyer_state_code) or itc_time_barred(bill))
    for item in bill.items.all():
        taxable, tax, *_ = calculate_item_tax_breakup(item, is_interstate=False)
        c, s, i = split_tax(tax, inter)
        parts = (('cgst', c), ('sgst', s), ('igst', i))
        out['taxable'] += taxable
        blocked = bool(getattr(item, 'itc_blocked', False))
        rcm = line_is_rcm(bill, item)
        if (only == 'rcm' and not rcm) or (only == 'non_rcm' and rcm):
            continue
        if rcm:
            out['vendor_payable'] += taxable
            out['rcm_taxable'] += taxable
            for k, v in parts:
                out['rcm_payable'][k] += v
        else:
            out['vendor_payable'] += taxable + tax
            if not registered:
                out['ineligible_tax'] += tax
                continue
        if lost:
            # 4D(2): not in 4A at all; cost in the books.
            for k, v in parts:
                out['lost_4d2'][k] += v
            out['lost_tax'] += tax
            continue
        gross = 'itc_4a_rcm' if rcm else 'itc_4a_normal'
        for k, v in parts:
            out[gross][k] += v
        if blocked:
            # s.17(5): shown in 4A and reversed in 4B(1); cost in the books.
            for k, v in parts:
                out['blocked_split'][k] += v
            out['blocked_tax'] += tax
        else:
            net = 'itc_rcm' if rcm else 'itc_normal'
            for k, v in parts:
                out[net][k] += v
    return out


def itc_summary(tenant, date_from, date_to):
    """GSTR-3B ITC/RCM buckets for [from, to]: non-RCM lines of bills dated in the period, plus
    RCM lines whose time of supply (s.12(3)/s.13(3), see rcm_documents) falls in the period."""
    from billing.gst_utils import get_seller_state_code
    from billing.models import PurchaseBill
    from datetime import date as _d
    if not isinstance(date_from, _d):
        date_from = _d.fromisoformat(str(date_from)[:10])
    if not isinstance(date_to, _d):
        date_to = _d.fromisoformat(str(date_to)[:10])
    buyer = get_seller_state_code(tenant)
    tot = {'itc_normal': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'itc_rcm': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'rcm_payable': {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
           'rcm_taxable': ZERO, 'blocked_tax': ZERO, 'ineligible_tax': ZERO, 'lost_tax': ZERO}
    for b in ('itc_4a_normal', 'itc_4a_rcm', 'blocked_split', 'lost_4d2'):
        tot[b] = {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO}
    # PurchaseBill has no draft/cancelled status in Cenvora; every saved bill counts.
    def _bills():
        # Non-RCM lines: by bill date.
        qs = PurchaseBill.objects.filter(created_by=tenant, bill_date__gte=date_from, bill_date__lte=date_to)
        for bill in qs.select_related('vendor', 'created_by').prefetch_related('items'):
            yield bill_tax_classification(bill, buyer, only='non_rcm')
        # RCM lines (Rudra, #219): by time of supply - goods s.12(3), services s.13(3). A time of
        # supply is never later than bill date + 61 days, so older bills can't land in this period.
        from datetime import timedelta
        from django.db.models import Q
        from billing.rcm_documents import rcm_time_of_supply
        from billing.gst_thresholds import is_services_sac
        rq = (PurchaseBill.objects.filter(created_by=tenant, bill_date__gte=date_from - timedelta(days=62))
              .filter(Q(is_reverse_charge=True) | Q(items__is_rcm=True)).distinct())
        for bill in rq.select_related('vendor', 'created_by').prefetch_related('items'):
            rcm_items = [it for it in bill.items.all() if line_is_rcm(bill, it)]
            if not rcm_items:
                continue
            # VERIFY: a bill mixing RCM goods and services uses the goods rule for all its RCM lines.
            is_service = all(is_services_sac(getattr(it, 'hsn_sac_code', '')) for it in rcm_items)
            tos = rcm_time_of_supply(bill, is_service)
            if date_from <= tos <= date_to:
                yield bill_tax_classification(bill, buyer, only='rcm')

    for b in _bills():
        for bucket in ('itc_normal', 'itc_rcm', 'rcm_payable', 'itc_4a_normal', 'itc_4a_rcm',
                       'blocked_split', 'lost_4d2'):
            for k in ('cgst', 'sgst', 'igst'):
                tot[bucket][k] += b[bucket][k]
        for k in ('rcm_taxable', 'blocked_tax', 'ineligible_tax', 'lost_tax'):
            tot[k] += b[k]
    return tot

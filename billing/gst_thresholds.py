"""
GST Shield rules (Rudra CA, 22 Oct 2026). Data-driven: thresholds live in STATE_GST_RULES,
not in branching logic. All money is Decimal.

Sources (verified 22 Oct 2026):
- Notification 10/2019-Central Tax, 7 Mar 2019 (₹40L goods-only exemption; excludes intra-State
  suppliers in Arunachal Pradesh, Manipur, Meghalaya, Mizoram, Nagaland, Puducherry, Sikkim,
  Telangana, Tripura, Uttarakhand; excludes ice cream 2105 00 00, pan masala 2106 90 20, Chapter 24 tobacco)
  https://gstcouncil.gov.in/sites/default/files/2024-05/notfctn-10-central-tax-english-2019.pdf
- s.22(1) CGST Act proviso (₹10L for special category States Manipur, Mizoram, Nagaland, Tripura),
  as summarised in Karnataka CT Dept "GST & MSMEs" booklet (as on 15.06.2019)
  https://gst.kar.nic.in/latestupdates/GSTMSMEsBooklet01072019.pdf
- Notification 14/2019-Central Tax, 7 Mar 2019 (composition ₹1.5Cr; ₹75L in Arunachal Pradesh,
  Manipur, Meghalaya, Mizoram, Nagaland, Sikkim, Tripura, Uttarakhand)
  https://gstcouncil.gov.in/sites/default/files/2024-05/notfctn-14-central-tax-english-2019.pdf
- Notification 02/2019-Central Tax (Rate) (services/mixed composition, ₹50L) via PIB PRID=1567975
  https://pib.gov.in/Pressreleaseshare.aspx?PRID=1567975
No later State opt-in/opt-out to the ₹40L goods limit was found in official sources.
"""
import datetime
from decimal import Decimal

L = Decimal('100000')
EFFECTIVE_2019 = datetime.date(2019, 4, 1)
N10 = 'Notfn 10/2019-CT; s.22 CGST'
N14 = 'Notfn 14/2019-CT'

# code: (name, goods_only_threshold, services_threshold, composition_goods_limit)
_T = {
    '01': ('Jammu and Kashmir', 40, 20, 150), '02': ('Himachal Pradesh', 40, 20, 150),
    '03': ('Punjab', 40, 20, 150), '04': ('Chandigarh', 40, 20, 150),
    '05': ('Uttarakhand', 20, 20, 75), '06': ('Haryana', 40, 20, 150),
    '07': ('Delhi', 40, 20, 150), '08': ('Rajasthan', 40, 20, 150),
    '09': ('Uttar Pradesh', 40, 20, 150), '10': ('Bihar', 40, 20, 150),
    '11': ('Sikkim', 20, 20, 75), '12': ('Arunachal Pradesh', 20, 20, 75),
    '13': ('Nagaland', 10, 10, 75), '14': ('Manipur', 10, 10, 75),
    '15': ('Mizoram', 10, 10, 75), '16': ('Tripura', 10, 10, 75),
    '17': ('Meghalaya', 20, 20, 75), '18': ('Assam', 40, 20, 150),
    '19': ('West Bengal', 40, 20, 150), '20': ('Jharkhand', 40, 20, 150),
    '21': ('Odisha', 40, 20, 150), '22': ('Chhattisgarh', 40, 20, 150),
    '23': ('Madhya Pradesh', 40, 20, 150), '24': ('Gujarat', 40, 20, 150),
    '26': ('Dadra and Nagar Haveli and Daman and Diu', 40, 20, 150),
    '27': ('Maharashtra', 40, 20, 150), '29': ('Karnataka', 40, 20, 150),
    '30': ('Goa', 40, 20, 150), '31': ('Lakshadweep', 40, 20, 150),
    '32': ('Kerala', 40, 20, 150), '33': ('Tamil Nadu', 40, 20, 150),
    '34': ('Puducherry', 20, 20, 150), '35': ('Andaman and Nicobar Islands', 40, 20, 150),
    '36': ('Telangana', 20, 20, 150), '37': ('Andhra Pradesh', 40, 20, 150),
    '38': ('Ladakh', 40, 20, 150),
}


def _category(goods, services):
    if services == 10:
        return 'special_10L'
    return 'goods_40L' if goods == 40 else 'goods_20L'


STATE_GST_RULES = {
    code: {
        'state_code': code, 'name': name,
        'category': _category(g, s),
        'goods_only_threshold': Decimal(g) * L,
        'services_threshold': Decimal(s) * L,
        'composition_goods_limit': Decimal(c) * L,
        'effective_from': EFFECTIVE_2019,
        'source': f'{N10}; {N14}',
    }
    for code, (name, g, s, c) in _T.items()
}

COMPOSITION_SERVICES_LIMIT = Decimal('50') * L      # Notfn 02/2019-CT(Rate)
QRMP_LIMIT = Decimal('500') * L                     # ₹5 Cr
HSN_6_DIGIT_ABOVE = Decimal('500') * L              # >₹5 Cr: 6-digit HSN, else 4
E_INVOICE_ABOVE = Decimal('500') * L                # ₹5 Cr (Cenvora does NOT support e-invoicing)
CONSERVATIVE_THRESHOLD = Decimal('20') * L          # unknown state / business type
SPECIAL_GOODS_HSN_PREFIXES = ('2105', '21069020', '24')  # ice cream, pan masala, tobacco
ALERT_PCT = Decimal('80')


def is_special_goods_hsn(hsn):
    h = ''.join(ch for ch in str(hsn or '') if ch.isdigit())
    return any(h.startswith(p) for p in SPECIAL_GOODS_HSN_PREFIXES)


def is_services_sac(hsn):
    h = ''.join(ch for ch in str(hsn or '') if ch.isdigit())
    return h.startswith('99')


def registration_threshold(state_code, business_type, deals_in_special_goods=False):
    """Return (threshold Decimal, basis str). Unknown inputs fall back conservatively."""
    rule = STATE_GST_RULES.get(str(state_code or '').zfill(2)) if state_code else None
    if not rule:
        return CONSERVATIVE_THRESHOLD, 'State unknown: conservative ₹20L'
    if business_type == 'goods_only' and not deals_in_special_goods:
        return rule['goods_only_threshold'], f"Goods only, {rule['name']} ({rule['category']})"
    if business_type == 'goods_only' and deals_in_special_goods:
        return rule['services_threshold'], f"Ice cream / pan masala / tobacco: {rule['name']}"
    if business_type in ('services', 'mixed'):
        return rule['services_threshold'], f"{'Services' if business_type == 'services' else 'Goods + services'}, {rule['name']}"
    # business_type not set: conservative ₹20L, or ₹10L where that is the state limit
    return min(CONSERVATIVE_THRESHOLD, rule['services_threshold']), f"Business type not set: conservative, {rule['name']}"


def composition_limit(state_code, business_type):
    if business_type == 'services':
        return COMPOSITION_SERVICES_LIMIT
    rule = STATE_GST_RULES.get(str(state_code or '').zfill(2))
    return rule['composition_goods_limit'] if rule else Decimal('75') * L


def fy_bounds(on_date):
    start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
    return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)


def month_bounds(on_date):
    import calendar
    return on_date.replace(day=1), on_date.replace(day=calendar.monthrange(on_date.year, on_date.month)[1])


# ── Computations (shared tax calculation from billing.gst_utils) ─────────────

def _owner_q(owner, prefix=''):
    from django.db.models import Q
    return Q(**{f'{prefix}created_by': owner}) | Q(**{f'{prefix}created_by__parent': owner})


def _invoices(owner, start, end):
    from billing.models import SalesInvoice
    return (SalesInvoice.objects.filter(_owner_q(owner), status='final', invoice_date__gte=start, invoice_date__lte=end)
            .select_related('customer').prefetch_related('items'))


def _credit_notes(owner, start, end):
    from billing.models_returns import CreditNote
    return (CreditNote.objects.filter(_owner_q(owner), date__gte=start, date__lte=end)
            .select_related('customer', 'original_invoice').prefetch_related('items'))


def sales_summary(owner, start, end):
    """Taxable value and output tax of outward supplies in [start, end], net of credit notes
    dated in the same window. Includes exempt/nil/export/inter-State (taxable value); excludes
    GST/cess and inward supplies. Also flags special goods and inter-State goods supplies."""
    from billing.gst_utils import (calculate_item_tax_breakup, credit_note_tax_breakup,
                                   get_pos_state_code, get_seller_state_code)
    seller = get_seller_state_code(owner)
    z = Decimal('0.00')
    taxable = tax = z
    special = inter_goods = False
    for inv in _invoices(owner, start, end):
        pos_raw = inv.place_of_supply or (inv.customer.state if inv.customer else None) or seller
        inter = seller != get_pos_state_code(pos_raw, fallback_state=seller)
        for item in inv.items.all():
            if getattr(item, 'row_type', 'item') == 'note':
                continue
            t, _tx, c, s, i, _r = calculate_item_tax_breakup(item, inter)
            taxable += t
            tax += c + s + i
            if is_special_goods_hsn(item.hsn_sac_code):
                special = True
            if inter and not is_services_sac(item.hsn_sac_code):
                inter_goods = True
    cn_taxable = cn_tax = z
    for cn in _credit_notes(owner, start, end):
        tot = credit_note_tax_breakup(cn, seller)['totals']
        cn_taxable += tot['taxable']
        cn_tax += tot['tax']
    return {
        'gross_taxable': taxable, 'credit_note_taxable': cn_taxable, 'credit_note_tax': cn_tax,
        'turnover': max(z, taxable - cn_taxable),
        'output_tax': tax - cn_tax,
        'deals_in_special_goods': special, 'has_interstate_goods': inter_goods,
    }


def itc_summary(owner, start, end):
    from billing.models import PurchaseBill
    from billing.services_gstr2b import calculate_purchase_bill_tax
    z = Decimal('0.00')
    itc = safe = at_risk = z
    bills = PurchaseBill.objects.filter(_owner_q(owner), bill_date__gte=start, bill_date__lte=end).prefetch_related('items')
    for b in bills:
        _tv, tax_amt, _c, _s, _i = calculate_purchase_bill_tax(b)
        itc += tax_amt
        if b.gstr2b_status == 'matched':
            safe += tax_amt
        else:
            at_risk += tax_amt
    return {'itc': itc, 'safe_itc': safe, 'at_risk_itc': at_risk}


def pct(value, limit):
    if not limit:
        return Decimal('0')
    return (Decimal(value) / Decimal(limit) * 100).quantize(Decimal('0.01'))


def gst_shield(owner, today, period='month'):
    """Card payload. period: 'month' (current return period) or 'fy' (FY to date)."""
    from billing.gst_utils import get_state_code_from_gstin, normalize_state_code
    gstin = (getattr(owner, 'gstin', '') or '').strip()
    state_code = (get_state_code_from_gstin(gstin) if gstin else None) or normalize_state_code(getattr(owner, 'state', '') or '') or None
    business_type = getattr(owner, 'business_type', None)
    registered = len(gstin) == 15

    fy_start, fy_end = fy_bounds(today)
    fy = sales_summary(owner, fy_start, today)
    p_start, p_end = (month_bounds(today) if period == 'month' else (fy_start, today))
    s = lambda d: str(Decimal(d).quantize(Decimal('0.01')))
    out = {
        'registered': registered, 'state_code': state_code, 'business_type': business_type,
        'fy_start': fy_start.isoformat(), 'fy_turnover': s(fy['turnover']),
        'compulsory_registration_interstate': (not registered) and fy['has_interstate_goods'],
        'period': period, 'period_start': p_start.isoformat(), 'period_end': p_end.isoformat(),
        'paid_available': False,  # no GSTR-3B cash payment record exists in Cenvora
    }
    if not registered:
        thr, basis = registration_threshold(state_code, business_type, fy['deals_in_special_goods'])
        pc = pct(fy['turnover'], thr)
        out.update({
            'registration_threshold': s(thr), 'threshold_basis': basis,
            'threshold_percent': str(pc), 'liable_to_register': fy['turnover'] > thr,
            'alert': 'exceeded' if pc >= 100 else ('approaching' if pc >= ALERT_PCT else None),
            'alert_text': ('Turnover has crossed the registration threshold. Register within 30 days.' if fy['turnover'] > thr
                           else 'Approaching the registration threshold. You must register within 30 days of crossing it.' if pc >= ALERT_PCT
                           else None),
        })
        return out

    prior_start = datetime.date(fy_start.year - 1, 4, 1)
    prior_end = datetime.date(fy_start.year, 3, 31)
    prior_sales = sales_summary(owner, prior_start, prior_end)
    prior = prior_sales['turnover']
    comp = composition_limit(state_code, business_type)
    sales = sales_summary(owner, p_start, p_end)
    itc = itc_summary(owner, p_start, p_end)
    net = sales['output_tax'] - itc['itc']
    out.update({
        'prior_fy_turnover': s(prior),
        'prior_fy_start': prior_start.isoformat(), 'prior_fy_end': prior_end.isoformat(),
        'prior_fy_gross_taxable': s(prior_sales['gross_taxable']),
        'prior_fy_credit_note_taxable': s(prior_sales['credit_note_taxable']),
        'output_tax_gross': s(sales['output_tax'] + sales['credit_note_tax']),
        'credit_note_tax': s(sales['credit_note_tax']),
        'prior_fy_label': f'FY {prior_start.year}-{str(prior_end.year)[2:]}',
        'composition_limit': s(comp), 'composition_eligible': prior <= comp,
        'qrmp_limit': s(QRMP_LIMIT), 'qrmp_eligible': prior <= QRMP_LIMIT,
        'hsn_digits': 4 if prior <= HSN_6_DIGIT_ABOVE else 6,
        'e_invoice_warning': ('Turnover exceeds ₹5 Cr: e-invoicing applies. Cenvora does not support e-invoicing.'
                              if max(prior, fy['turnover']) > E_INVOICE_ABOVE else None),
        'output_tax': s(sales['output_tax']), 'itc': s(itc['itc']),
        'net_payable': s(net), 'safe_itc': s(itc['safe_itc']), 'at_risk_itc': s(itc['at_risk_itc']),
    })
    return out

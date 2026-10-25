"""
GST State Utilities and Standards for Indian GST Compliance.
Provides authoritative state code mappings, GSTIN parsing,
seller state extraction, place-of-supply resolution, and interstate determination.
"""
from decimal import Decimal
import re

STATE_NAME_TO_CODE = {
    'JAMMU AND KASHMIR': '01',
    'JAMMU & KASHMIR': '01',
    'JK': '01',
    'HIMACHAL PRADESH': '02',
    'HP': '02',
    'PUNJAB': '03',
    'PB': '03',
    'CHANDIGARH': '04',
    'CH': '04',
    'UTTARAKHAND': '05',
    'UK': '05',
    'UA': '05',
    'HARYANA': '06',
    'HR': '06',
    'DELHI': '07',
    'DL': '07',
    'RAJASTHAN': '08',
    'RJ': '08',
    'UTTAR PRADESH': '09',
    'UP': '09',
    'BIHAR': '10',
    'BR': '10',
    'SIKKIM': '11',
    'SK': '11',
    'ARUNACHAL PRADESH': '12',
    'AR': '12',
    'NAGALAND': '13',
    'NL': '13',
    'MANIPUR': '14',
    'MN': '14',
    'MIZORAM': '15',
    'MZ': '15',
    'TRIPURA': '16',
    'TR': '16',
    'MEGHALAYA': '17',
    'ML': '17',
    'ASSAM': '18',
    'AS': '18',
    'WEST BENGAL': '19',
    'WB': '19',
    'JHARKHAND': '20',
    'JH': '20',
    'ODISHA': '21',
    'ORISSA': '21',
    'OR': '21',
    'OD': '21',
    'CHHATTISGARH': '22',
    'CG': '22',
    'CT': '22',
    'MADHYA PRADESH': '23',
    'MP': '23',
    'GUJARAT': '24',
    'GJ': '24',
    'DAMAN AND DIU': '25',
    'DD': '25',
    'DADRA AND NAGAR HAVELI AND DAMAN AND DIU': '26',
    'DADRA AND NAGAR HAVELI': '26',
    'DN': '26',
    'MAHARASHTRA': '27',
    'MH': '27',
    'ANDHRA PRADESH (BEFORE DIVISION)': '28',
    'KARNATAKA': '29',
    'KA': '29',
    'GOA': '30',
    'GA': '30',
    'LAKSHADWEEP': '31',
    'LD': '31',
    'KERALA': '32',
    'KL': '32',
    'TAMIL NADU': '33',
    'TN': '33',
    'PUDUCHERRY': '34',
    'PONDICHERRY': '34',
    'PY': '34',
    'ANDAMAN AND NICOBAR ISLANDS': '35',
    'AN': '35',
    'TELANGANA': '36',
    'TS': '36',
    'TG': '36',
    'ANDHRA PRADESH': '37',
    'AP': '37',
    'LADAKH': '38',
    'LA': '38',
    'OTHER TERRITORY': '97',
    'OT': '97',
}

CODE_TO_STATE_NAME = {
    '01': 'Jammu and Kashmir',
    '02': 'Himachal Pradesh',
    '03': 'Punjab',
    '04': 'Chandigarh',
    '05': 'Uttarakhand',
    '06': 'Haryana',
    '07': 'Delhi',
    '08': 'Rajasthan',
    '09': 'Uttar Pradesh',
    '10': 'Bihar',
    '11': 'Sikkim',
    '12': 'Arunachal Pradesh',
    '13': 'Nagaland',
    '14': 'Manipur',
    '15': 'Mizoram',
    '16': 'Tripura',
    '17': 'Meghalaya',
    '18': 'Assam',
    '19': 'West Bengal',
    '20': 'Jharkhand',
    '21': 'Odisha',
    '22': 'Chhattisgarh',
    '23': 'Madhya Pradesh',
    '24': 'Gujarat',
    '25': 'Daman and Diu',
    '26': 'Dadra and Nagar Haveli and Daman and Diu',
    '27': 'Maharashtra',
    '28': 'Andhra Pradesh',
    '29': 'Karnataka',
    '30': 'Goa',
    '31': 'Lakshadweep',
    '32': 'Kerala',
    '33': 'Tamil Nadu',
    '34': 'Puducherry',
    '35': 'Andaman and Nicobar Islands',
    '36': 'Telangana',
    '37': 'Andhra Pradesh',
    '38': 'Ladakh',
    '97': 'Other Territory',
}


def normalize_state_code(val):
    """
    Given any state representation (e.g. 'Odisha', 'OR', '21', 21, 'Karnataka', 'KA', '29'),
    returns the official 2-digit GST state code as a zero-padded string (e.g. '21', '29').
    Returns None if unknown.
    """
    if val is None:
        return None
    val_str = str(val).strip().upper()
    if not val_str:
        return None

    # Check if already 1 or 2 digits
    if val_str.isdigit():
        code_int = int(val_str)
        code_str = f"{code_int:02d}"
        if code_str in CODE_TO_STATE_NAME:
            return code_str

    # Direct name or abbreviation lookup
    if val_str in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[val_str]

    # Partial match cleanup
    cleaned = re.sub(r'[^A-Z0-9]', ' ', val_str).strip()
    cleaned = ' '.join(cleaned.split())
    if cleaned in STATE_NAME_TO_CODE:
        return STATE_NAME_TO_CODE[cleaned]

    return None


def get_state_code_from_gstin(gstin):
    """
    Extracts the 2-digit state code from the first 2 characters of a GSTIN.
    Validates that the extracted code is a valid Indian state code.
    """
    if not gstin:
        return None
    clean_gstin = str(gstin).strip().upper()
    if len(clean_gstin) >= 2 and clean_gstin[:2].isdigit():
        code = clean_gstin[:2]
        if code in CODE_TO_STATE_NAME:
            return code
    return None


def get_seller_state_code(tenant_or_user):
    """
    CA AUDIT REQUIREMENT (Issue 2):
    The choice between intra-state and inter-state MUST come from the seller GSTIN
    (first 2 digits), NOT the company state field.
    Falls back to tenant.state only if GSTIN is not present or invalid.
    """
    if not tenant_or_user:
        return '21'  # Default demo state code

    gstin = getattr(tenant_or_user, 'gstin', None)
    if gstin:
        gstin_code = get_state_code_from_gstin(gstin)
        if gstin_code:
            return gstin_code

    state = getattr(tenant_or_user, 'state', None)
    if state:
        state_code = normalize_state_code(state)
        if state_code:
            return state_code

    return '21'


def get_pos_state_code(pos, fallback_state=None):
    """
    Resolves the Place of Supply into a 2-digit state code.
    Accepts state name, state code, abbreviation, or customer GSTIN.
    """
    if pos:
        # Check if pos is a full 15-char GSTIN
        if len(str(pos).strip()) == 15 and str(pos).strip()[:2].isdigit():
            c = get_state_code_from_gstin(pos)
            if c:
                return c
        c = normalize_state_code(pos)
        if c:
            return c

    if fallback_state:
        c = normalize_state_code(fallback_state)
        if c:
            return c

    return '21'


def is_interstate_supply(seller_state_code_or_obj, pos_or_customer_state):
    """
    Determines whether a supply is inter-state (IGST) or intra-state (CGST + SGST).
    Takes either 2-digit codes or entity objects.
    """
    if isinstance(seller_state_code_or_obj, str) and len(seller_state_code_or_obj) == 2 and seller_state_code_or_obj.isdigit():
        seller_code = seller_state_code_or_obj
    else:
        seller_code = get_seller_state_code(seller_state_code_or_obj)

    pos_code = get_pos_state_code(pos_or_customer_state)

    if not seller_code or not pos_code:
        return False

    return str(seller_code).strip() != str(pos_code).strip()


# GST slab rates used in the product UI; values above this are treated as absolute tax amounts
# (legacy/seed rows sometimes stored tax amount in the `tax` column instead of the %).
_MAX_GST_RATE_PERCENT = Decimal('40')


def calculate_item_tax_breakup(item, is_interstate=False):
    """
    Standard tax calculation for SalesInvoiceItem, PurchaseBillItem, CreditNoteItem, DebitNoteItem.
    Returns: (taxable_amount, tax_amount, cgst, sgst, igst, tax_rate_or_field)
    """
    quantity = Decimal(str(getattr(item, 'quantity', 0) or 0))
    price = Decimal(str(getattr(item, 'price', 0) or 0))
    discount = Decimal(str(getattr(item, 'discount', 0) or 0))
    line_amount = Decimal(str(getattr(item, 'amount', 0) or 0))
    tax_field = Decimal(str(getattr(item, 'tax', 0) or 0))

    base_amount = quantity * price
    discount_amount = ((base_amount * discount) / Decimal('100')).quantize(Decimal('0.01'))
    calc_taxable = (base_amount - discount_amount).quantize(Decimal('0.01'))

    # Detect legacy rows where `tax` holds an absolute amount rather than a %.
    tax_is_absolute = tax_field > _MAX_GST_RATE_PERCENT
    tax_rate = Decimal('0.00') if tax_is_absolute else tax_field

    if quantity > 0 and price > 0:
        taxable_amount = calc_taxable
        if tax_is_absolute:
            tax_amount = tax_field.quantize(Decimal('0.01'))
            if line_amount > taxable_amount:
                embedded = (line_amount - taxable_amount).quantize(Decimal('0.01'))
                # Prefer gross-embedded tax when present (and when absolute field is implausible)
                if abs(embedded - tax_amount) <= Decimal('0.05') or tax_amount > taxable_amount:
                    tax_amount = embedded
        elif tax_rate > 0:
            tax_amount = ((taxable_amount * tax_rate) / Decimal('100')).quantize(Decimal('0.01'))
        elif line_amount > taxable_amount:
            tax_amount = line_amount - taxable_amount
        else:
            tax_amount = Decimal('0.00')
    else:
        if tax_is_absolute and line_amount > 0:
            if line_amount > tax_field:
                taxable_amount = (line_amount - tax_field).quantize(Decimal('0.01'))
                tax_amount = tax_field.quantize(Decimal('0.01'))
            else:
                taxable_amount = line_amount
                tax_amount = tax_field.quantize(Decimal('0.01'))
        elif tax_rate > 0 and line_amount > 0:
            taxable_amount = (line_amount / (Decimal('1') + (tax_rate / Decimal('100')))).quantize(Decimal('0.01'))
            tax_amount = line_amount - taxable_amount
        else:
            taxable_amount = line_amount
            tax_amount = Decimal('0.00')

    if is_interstate:
        igst = tax_amount
        cgst = Decimal('0.00')
        sgst = Decimal('0.00')
    else:
        cgst = (tax_amount / Decimal('2')).quantize(Decimal('0.01'))
        sgst = tax_amount - cgst
        igst = Decimal('0.00')

    return taxable_amount, tax_amount, cgst, sgst, igst, tax_rate if not tax_is_absolute else tax_field


def get_debit_note_supplier_state_code(debit_note, buyer_state_code):
    """
    Resolve supplier POS for a purchase return (debit note).

    Must stay aligned with AccountingService.create_debit_note_entries so GSTR-3B
    Table 4(B) ITC reversals match Input IGST/CGST/SGST ledger credits.

    Preference (GSTIN-first, same spirit as purchase bills):
      1. debit_note.vendor_gstin
      2. original_bill.vendor_gstin / vendor.gstin
      3. original_bill.vendor.state
      4. buyer_state_code fallback (treat as intra-state)
    """
    original_bill = getattr(debit_note, 'original_bill', None)
    vendor = getattr(original_bill, 'vendor', None) if original_bill is not None else None
    vendor_gstin = (
        getattr(debit_note, 'vendor_gstin', None)
        or (getattr(original_bill, 'vendor_gstin', None) if original_bill is not None else None)
        or (getattr(vendor, 'gstin', None) if vendor is not None else None)
    )
    vendor_state = getattr(vendor, 'state', None) if vendor is not None else None
    return get_pos_state_code(vendor_gstin or vendor_state, fallback_state=buyer_state_code)


def get_purchase_supplier_state_code(bill, buyer_state_code):
    """
    Resolve supplier state for a purchase bill (ITC available / Table 4(A)).
    Prefer GSTIN (bill.vendor_gstin or vendor.gstin) over free-text state.
    Returns None when the vendor cannot be placed — do not silently use the buyer
    (that forced intra-state CGST/SGST on interstate IGST bills).
    """
    vendor = getattr(bill, 'vendor', None)
    vendor_gstin = (
        getattr(bill, 'vendor_gstin', None)
        or (getattr(vendor, 'gstin', None) if vendor else None)
    )
    vendor_state = getattr(vendor, 'state', None) if vendor else None
    if vendor_gstin and len(str(vendor_gstin).strip()) == 15:
        code = get_state_code_from_gstin(vendor_gstin)
        if code:
            return code
    if vendor_state or vendor_gstin:
        code = normalize_state_code(vendor_state) or get_state_code_from_gstin(vendor_gstin)
        if code:
            return code
    return None


def validate_gstin_state_match(gstin, state):
    """
    Verifies that the state code prefix in GSTIN matches the chosen state.
    Returns (is_valid, gstin_state_code, state_code, error_message).
    """
    if not gstin or not state:
        return True, None, None, None

    gstin_code = get_state_code_from_gstin(gstin)
    state_code = normalize_state_code(state)

    if not gstin_code or not state_code:
        return True, gstin_code, state_code, None

    if gstin_code != state_code:
        gstin_state_name = CODE_TO_STATE_NAME.get(gstin_code, f"State code {gstin_code}")
        selected_state_name = CODE_TO_STATE_NAME.get(state_code, state)
        msg = (
            f"Seller GSTIN starts with '{gstin_code}' ({gstin_state_name}), "
            f"which does not match the selected company state '{selected_state_name}' ({state_code})."
        )
        return False, gstin_code, state_code, msg

    return True, gstin_code, state_code, None


UQC_MAP = {
    'pcs': 'NOS', 'piece': 'NOS', 'pieces': 'NOS', 'nos': 'NOS', 'no': 'NOS',
    'kg': 'KGS', 'kgs': 'KGS', 'kilogram': 'KGS', 'kilograms': 'KGS',
    'g': 'GMS', 'gms': 'GMS', 'gram': 'GMS', 'grams': 'GMS',
    'm': 'MTR', 'meter': 'MTR', 'meters': 'MTR',
    'box': 'BOX', 'boxes': 'BOX',
    'pack': 'PAC', 'packs': 'PAC',
    'dozen': 'DOZ',
    'l': 'LTR', 'ltr': 'LTR', 'litre': 'LTR', 'litres': 'LTR',
    'can': 'CAN', 'bag': 'BAG', 'bags': 'BAG', 'set': 'SET', 'sets': 'SET',
    'sqm': 'SQM', 'ton': 'TON', 'tons': 'TON', 'roll': 'ROL', 'rolls': 'ROL',
    'bottle': 'BTL', 'bottles': 'BTL',
}


def get_uqc(unit):
    """Normalize unit string into standard GST UQC code (e.g., NOS, KGS)."""
    if not unit:
        return 'NOS'
    clean = str(unit).strip().lower()
    return UQC_MAP.get(clean, 'NOS' if not clean.isalnum() else clean.upper()[:3])


def get_required_hsn_digits(tenant=None):
    """
    Indian GST Rule (Notification No. 78/2020 - Central Tax):
    4 digits when turnover up to ₹5 crore, 6 digits above that.
    """
    turnover = getattr(tenant, 'annual_turnover', None) if tenant else None
    if turnover is None and tenant:
        try:
            from billing.models import SalesInvoice
            from django.db.models import Sum
            turnover = SalesInvoice.objects.filter(created_by=tenant, status='final').aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0.00')
        except Exception:
            turnover = Decimal('0.00')
    if turnover and Decimal(str(turnover)) > Decimal('50000000'):
        return 6
    return 4


# Single shared HSN copy for product, invoice, and purchase forms (BE + FE must match).
HSN_REQUIRED_MSG = 'HSN/SAC code is required.'
HSN_DIGITS_MSG = 'HSN/SAC code must be at least 4 digits (6 digits if turnover > ₹5 Cr).'


def validate_hsn_sac_code(code, tenant=None):
    """
    Validate HSN/SAC. Returns cleaned string or raises ValueError with HSN_*_MSG.
    """
    import re
    hsn_str = str(code or '').strip()
    if not hsn_str or hsn_str.upper() == 'N/A':
        raise ValueError(HSN_REQUIRED_MSG)
    clean_digits = re.sub(r'\D', '', hsn_str)
    min_digits = get_required_hsn_digits(tenant) if tenant else 4
    if len(clean_digits) < min_digits:
        raise ValueError(HSN_DIGITS_MSG)
    return hsn_str


def clean_hsn_sac_code(code, default_service=False):
    """Cleans HSN/SAC code ensuring it is never 'N/A' or blank."""
    if not code:
        return '998311' if default_service else '847130'
    c = str(code).strip()
    if not c or c.upper() == 'N/A':
        return '998311' if default_service else '847130'
    return c


GSTIN_FORMAT_REGEX = re.compile(
    r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
)
GSTIN_INVALID_MSG = 'Invalid GSTIN format'


def validate_gstin_format(value, required=False):
    """
    Validate Indian GSTIN format. Returns normalized uppercase GSTIN or None/''.
    Raises ValueError with GSTIN_INVALID_MSG when format is wrong.
    Empty is allowed when required=False.
    """
    gstin = (value or '').strip().upper()
    if not gstin:
        if required:
            raise ValueError('GSTIN is required')
        return gstin
    if not GSTIN_FORMAT_REGEX.fullmatch(gstin):
        raise ValueError(GSTIN_INVALID_MSG)
    return gstin



def credit_note_tax_breakup(credit_note, seller_state_code=None):
    """
    Single source of truth for a credit note's GST split, shared by GSTR-1 CDNR/CDNUR
    and the Credit Note detail API. All values Decimal.

    Returns dict: pos_code, is_interstate, lines=[{item, taxable, rate, cgst, sgst, igst, tax}],
    totals={taxable, cgst, sgst, igst, cess, tax, total}.
    """
    if seller_state_code is None:
        user = credit_note.created_by
        seller_state_code = get_seller_state_code(getattr(user, 'active_tenant', user))
    orig_inv = credit_note.original_invoice
    customer = credit_note.customer
    pos_raw = (
        (orig_inv.place_of_supply if orig_inv else None)
        or (customer.state if customer else None)
        or seller_state_code
    )
    pos_code = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
    is_inter = (seller_state_code != pos_code)

    z = Decimal('0.00')
    totals = {'taxable': z, 'cgst': z, 'sgst': z, 'igst': z, 'cess': z}
    lines = []
    for item in credit_note.items.all():
        taxable, tax, cgst, sgst, igst, rt = calculate_item_tax_breakup(item, is_inter)
        lines.append({'item': item, 'taxable': taxable, 'rate': Decimal(str(rt)),
                      'cgst': cgst, 'sgst': sgst, 'igst': igst, 'tax': cgst + sgst + igst})
        totals['taxable'] += taxable
        totals['cgst'] += cgst
        totals['sgst'] += sgst
        totals['igst'] += igst
    totals['tax'] = totals['cgst'] + totals['sgst'] + totals['igst'] + totals['cess']
    totals['total'] = (totals['taxable'] + totals['tax']).quantize(Decimal('0.01'))
    return {'pos_code': pos_code, 'is_interstate': is_inter, 'lines': lines, 'totals': totals}

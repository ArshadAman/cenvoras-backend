"""
UAT P1 item 5 (Rudra CA): quantity precision by UQC. Quantities are never rounded silently.

* Count units (NOS/PCS/UNT/BOX/SET/PRS/DOZ) take whole numbers only.
* Measured units (KGS/GMS/LTR/MLT/MTR/CMS/SQM/CBM/TON/QTL) take up to 3 decimals.

Product.unit is free text in Cenvora ("pcs", "kg", "ml", ...), so common spellings are mapped
to the GST UQC first. A unit we can't map is treated as a count unit (whole numbers), the
conservative choice, and the error names the unit so the user can fix the product.
"""
from decimal import Decimal, InvalidOperation

COUNT_UQCS = ('NOS', 'PCS', 'UNT', 'BOX', 'SET', 'PRS', 'DOZ')
MEASURED_UQCS = ('KGS', 'GMS', 'LTR', 'MLT', 'MTR', 'CMS', 'SQM', 'CBM', 'TON', 'QTL')
MEASURED_DECIMALS = 3

_ALIASES = {
    # count
    'NOS': 'NOS', 'NO': 'NOS', 'NUMBER': 'NOS', 'NUMBERS': 'NOS',
    'PCS': 'PCS', 'PC': 'PCS', 'PIECE': 'PCS', 'PIECES': 'PCS',
    'UNT': 'UNT', 'UNIT': 'UNT', 'UNITS': 'UNT',
    'BOX': 'BOX', 'BOXES': 'BOX',
    'SET': 'SET', 'SETS': 'SET',
    'PRS': 'PRS', 'PAIR': 'PRS', 'PAIRS': 'PRS',
    'DOZ': 'DOZ', 'DOZEN': 'DOZ', 'DOZENS': 'DOZ',
    # measured
    'KGS': 'KGS', 'KG': 'KGS', 'KILOGRAM': 'KGS', 'KILOGRAMS': 'KGS',
    'GMS': 'GMS', 'G': 'GMS', 'GM': 'GMS', 'GRAM': 'GMS', 'GRAMS': 'GMS',
    'LTR': 'LTR', 'L': 'LTR', 'LITRE': 'LTR', 'LITRES': 'LTR', 'LITER': 'LTR', 'LITERS': 'LTR',
    'MLT': 'MLT', 'ML': 'MLT', 'MILLILITRE': 'MLT',
    'MTR': 'MTR', 'M': 'MTR', 'METER': 'MTR', 'METRE': 'MTR', 'METERS': 'MTR', 'METRES': 'MTR',
    'CMS': 'CMS', 'CM': 'CMS', 'CENTIMETER': 'CMS', 'CENTIMETRE': 'CMS',
    'SQM': 'SQM', 'SQMTR': 'SQM', 'SQ M': 'SQM', 'SQ.M': 'SQM',
    'CBM': 'CBM', 'CUBIC METER': 'CBM', 'CUBIC METRE': 'CBM',
    'TON': 'TON', 'TONS': 'TON', 'TONNE': 'TON', 'TONNES': 'TON', 'MT': 'TON',
    'QTL': 'QTL', 'QUINTAL': 'QTL', 'QUINTALS': 'QTL',
}


def to_uqc(unit):
    """Map a free-text unit to a GST UQC, or None when unknown."""
    key = ' '.join(str(unit or '').strip().upper().split())
    return _ALIASES.get(key)


def is_measured(unit):
    return to_uqc(unit) in MEASURED_UQCS


def quantity_error(quantity, unit):
    """Return an error message for `quantity` in `unit`, or None when it is acceptable."""
    try:
        q = Decimal(str(quantity))
    except (InvalidOperation, TypeError, ValueError):
        return 'Enter a valid quantity.'
    if not q.is_finite():
        return 'Enter a valid quantity.'
    label = str(unit or '').strip() or 'units'
    if is_measured(unit):
        if q != q.quantize(Decimal(1).scaleb(-MEASURED_DECIMALS)):
            return f'{label} quantities can have at most {MEASURED_DECIMALS} decimals (got {q.normalize()}).'
        return None
    if q != q.to_integral_value():
        return (f'{label} is counted in whole units, so the quantity must be a whole number '
                f'(got {q.normalize()}).')
    return None

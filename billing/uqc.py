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


def to_qty(value, default='0'):
    """Quantity as an exact Decimal (stock is decimal(15,3)). Never rounds; raises on garbage."""
    if value is None or value == '':
        value = default
    try:
        q = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Enter a valid quantity.')
    if not q.is_finite():
        raise ValueError('Enter a valid quantity.')
    return q


QTY_FIELDS = ('quantity', 'free_quantity', 'required_quantity')


def line_quantity_errors(attrs, fields=QTY_FIELDS):
    """UQC check for one line: {field: message} for quantities that don't fit the line's unit."""
    product = attrs.get('product')
    unit = attrs.get('unit') or getattr(product, 'unit', None)
    name = getattr(product, 'name', '') or 'item'
    errors = {}
    for f in fields:
        if f in attrs and attrs[f] not in (None, ''):
            q = attrs[f]
            # A stock journal line is signed (+ add / − deduct): check the magnitude.
            try:
                q = abs(Decimal(str(q)))
            except (InvalidOperation, TypeError, ValueError):
                pass
            err = quantity_error(q, unit)
            if err:
                errors[f] = f'{name}: {err}'
    return errors


def fmt_qty(q):
    """Human quantity: 4.000 -> '4', 1.500 -> '1.5' (exact, never rounded)."""
    d = Decimal(str(q or 0))
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    return format(d.normalize(), 'f')


NUMBER_QTY_FIELDS = QTY_FIELDS + ('dispatched_quantity', 'pending_quantity', 'stock', 'current_stock')


class QuantityAsNumberMixin:
    """Stock/line quantities are decimal(15,3) but stay JSON numbers in the API (2, 1.5), as before."""

    def get_fields(self):
        from rest_framework import serializers
        fields = super().get_fields()
        for name in NUMBER_QTY_FIELDS:
            f = fields.get(name)
            if isinstance(f, serializers.DecimalField):
                f.coerce_to_string = False
        return fields


class UQCQuantityMixin(QuantityAsNumberMixin):
    """Serializer mixin: apply the UQC rule (count units whole, measured units ≤ 3 decimals) to line quantities."""

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        if ret.get('row_type') == 'note':
            return ret
        errors = line_quantity_errors(ret)
        if errors:
            from rest_framework import serializers
            raise serializers.ValidationError(errors)
        return ret

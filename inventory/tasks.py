import csv
import logging
import re
from io import StringIO
from celery import shared_task
from django.db import transaction
from inventory.serializers import ProductSerializer
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


def _extract_numeric(value):
    """Extract numeric part from loose values like '18%', 'GST 18', '1,234.50'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value).strip().lower()
    if not text:
        return None

    text = text.replace(',', '')
    text = text.replace('%', '')
    text = text.replace('gst', '')
    text = text.replace('tax', '')
    text = text.strip()

    # Fast path for clean numeric strings.
    if re.fullmatch(r'-?\d+(?:\.\d+)?', text):
        return text

    # Fallback: find first numeric token in noisy strings.
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    return match.group(0) if match else None

class FakeRequest:
    def __init__(self, user):
        self.user = user

@shared_task
def process_bulk_upload_csv(csv_content: str, user_id: str):
    user = User.objects.get(id=user_id)
    fake_request = FakeRequest(user)
    reader = csv.DictReader(StringIO(csv_content))

    def normalize_key(key):
        return (key or '').strip().lower().replace(' ', '_').replace('-', '_')

    header_aliases = {
        'item_code': ['item_code', 'itemcode', 'item_id', 'itemid', 'sku', 'product_code', 'product_id', 'code'],
        'name': ['name', 'product_name', 'item_name', 'product', 'item'],
        'unit': ['unit', 'uom', 'unit_of_measure', 'measurement_unit'],
        'cost_price': ['cost_price', 'price', 'purchase_price', 'cost'],
        'sale_price': ['sale_price', 'sales_price', 'selling_price', 'saleprice', 'salesprice'],
        'hsn_sac_code': ['hsn_sac_code', 'hsn_code', 'hsn'],
        'tax': ['tax', 'gst', 'gst_rate', 'tax_rate'],
        'low_stock_alert': ['low_stock_alert', 'min_stock_level', 'reorder_level'],
        'stock': ['stock', 'opening_stock', 'current_stock'],
        'secondary_unit': ['secondary_unit', 'secondaryunit'],
        'conversion_factor': ['conversion_factor', 'conversionfactor'],
        'warranty_months': ['warranty_months', 'warranty', 'warranty_month'],
        'manufacturer': ['manufacturer', 'mfg', 'mfg_by', 'brand', 'company', 'make'],
        'internal_reference': ['internal_reference', 'internal_ref', 'internalref', 'reference', 'ref_no', 'ref'],
    }

    expected_fields = ['item_code', 'name', 'hsn_sac_code', 'description', 'manufacturer', 'internal_reference', 'tax', 'stock', 'unit', 'secondary_unit', 'conversion_factor', 'cost_price', 'sale_price', 'low_stock_alert', 'warranty_months']
    optional_nullable_fields = {'item_code', 'hsn_sac_code', 'description', 'manufacturer', 'internal_reference', 'secondary_unit', 'sale_price'}
    # unit is optional — missing/blank column defaults to 'pcs'; any provided string is accepted as-is
    optional_with_default_fields = {'unit'}
    integer_fields = {'stock', 'conversion_factor', 'low_stock_alert', 'warranty_months'}
    decimal_fields = {'tax', 'cost_price', 'sale_price'}

    # Common aliases normalized to their canonical form (stored as-is from CSV otherwise)
    unit_aliases = {
        'nos': 'pcs',
        'no': 'pcs',
        'piece': 'pcs',
        'pieces': 'pcs',
        'pc': 'pcs',
        'pcs': 'pcs',
        'unit': 'pcs',
        'units': 'pcs',
        'number': 'pcs',
        'each': 'pcs',
        'ea': 'pcs',
        'ltr': 'l',
        'litre': 'l',
        'liter': 'l',
        'litres': 'l',
        'liters': 'l',
        'kgs': 'kg',
        'kilogram': 'kg',
        'kilograms': 'kg',
        'gm': 'g',
        'gram': 'g',
        'grams': 'g',
        'milligram': 'mg',
        'milligrams': 'mg',
        'milliliter': 'ml',
        'milliliters': 'ml',
        'millilitre': 'ml',
        'millilitres': 'ml',
        'mtr': 'm',
        'meter': 'm',
        'meters': 'm',
        'metre': 'm',
        'metres': 'm',
        'centimeter': 'cm',
        'centimeters': 'cm',
    }

    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    with transaction.atomic():
        for index, row in enumerate(reader, start=2):
            normalized_row = {normalize_key(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            if not any(v not in (None, '') for v in normalized_row.values()):
                continue

            payload = {}
            for field in expected_fields:
                lookup_key = 'cost_price' if field == 'cost_price' else field
                value = normalized_row.get(lookup_key)
                if value in (None, ''):
                    for alias in header_aliases.get(lookup_key, []):
                        alias_value = normalized_row.get(alias)
                        if alias_value not in (None, ''):
                            value = alias_value
                            break

                if value in (None, ''):
                    if field in optional_nullable_fields:
                        payload[field] = None
                    elif field in optional_with_default_fields:
                        # unit: if missing/blank, use model default 'pcs'
                        payload[field] = 'pcs'
                    continue

                if field == 'unit' and isinstance(value, str):
                    # Normalize common aliases; any unrecognized value is stored as-is (free-text)
                    normalized_unit = value.strip().lower()
                    value = unit_aliases.get(normalized_unit, value.strip())

                if field in integer_fields:
                    numeric_value = _extract_numeric(value)
                    try:
                        value = int(float(numeric_value))
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid integer value.']}})
                        payload = None
                        break

                if field in decimal_fields:
                    numeric_value = _extract_numeric(value)
                    try:
                        value = float(numeric_value)
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid number value.']}})
                        payload = None
                        break

                payload[field] = value

            if payload is None:
                continue

            # Check if this product already exists by user-defined item_code
            incoming_item_code = payload.get('item_code')
            existing_product = None
            if incoming_item_code:
                existing_product = Product.objects.filter(
                    created_by=user.active_tenant,
                    item_code__iexact=str(incoming_item_code).strip()
                ).first()

            if existing_product:
                diff_fields = {}

                # Name
                if 'name' in payload and payload['name'] is not None:
                    clean_name = str(payload['name']).strip()
                    if clean_name != existing_product.name:
                        diff_fields['name'] = clean_name

                # Unit
                if 'unit' in payload and payload['unit'] is not None:
                    if payload['unit'] != existing_product.unit:
                        diff_fields['unit'] = payload['unit']

                # Cost Price (stored as price on Product model)
                if 'cost_price' in payload and payload['cost_price'] is not None:
                    existing_cost = float(existing_product.price or 0)
                    if round(float(payload['cost_price']), 4) != round(existing_cost, 4):
                        diff_fields['price'] = payload['cost_price']

                # Sale Price
                if 'sale_price' in payload and payload['sale_price'] is not None:
                    existing_sale = float(existing_product.sale_price or 0) if existing_product.sale_price is not None else None
                    if existing_sale is None or round(float(payload['sale_price']), 4) != round(existing_sale, 4):
                        diff_fields['sale_price'] = payload['sale_price']

                # Stock
                if 'stock' in payload and payload['stock'] is not None:
                    if int(payload['stock']) != int(existing_product.stock or 0):
                        diff_fields['stock'] = payload['stock']

                # Tax
                if 'tax' in payload and payload['tax'] is not None:
                    existing_tax = float(existing_product.tax or 0)
                    if round(float(payload['tax']), 2) != round(existing_tax, 2):
                        diff_fields['tax'] = payload['tax']

                # HSN / SAC Code
                if 'hsn_sac_code' in payload and payload['hsn_sac_code'] != (existing_product.hsn_sac_code or None):
                    diff_fields['hsn_sac_code'] = payload['hsn_sac_code']

                # Manufacturer
                if 'manufacturer' in payload and payload['manufacturer'] != (existing_product.manufacturer or None):
                    diff_fields['manufacturer'] = payload['manufacturer']

                # Internal Reference
                if 'internal_reference' in payload and payload['internal_reference'] != (existing_product.internal_reference or None):
                    diff_fields['internal_reference'] = payload['internal_reference']

                # Description
                if 'description' in payload and payload['description'] != (existing_product.description or None):
                    diff_fields['description'] = payload['description']

                # Secondary Unit
                if 'secondary_unit' in payload and payload['secondary_unit'] != (existing_product.secondary_unit or None):
                    diff_fields['secondary_unit'] = payload['secondary_unit']

                # Conversion Factor
                if 'conversion_factor' in payload and payload['conversion_factor'] is not None:
                    if int(payload['conversion_factor']) != int(existing_product.conversion_factor or 1):
                        diff_fields['conversion_factor'] = payload['conversion_factor']

                # Low Stock Alert
                if 'low_stock_alert' in payload and payload['low_stock_alert'] is not None:
                    if int(payload['low_stock_alert']) != int(existing_product.low_stock_alert or 0):
                        diff_fields['low_stock_alert'] = payload['low_stock_alert']

                # Warranty Months
                if 'warranty_months' in payload and payload['warranty_months'] is not None:
                    if int(payload['warranty_months']) != int(existing_product.warranty_months or 0):
                        diff_fields['warranty_months'] = payload['warranty_months']

                if not diff_fields:
                    # All fields identical: skip reimporting, unchanged
                    skipped_count += 1
                else:
                    for attr, val in diff_fields.items():
                        setattr(existing_product, attr, val)
                    existing_product.save(update_fields=list(diff_fields.keys()))
                    updated_count += 1
            else:
                serializer = ProductSerializer(data=payload, context={'request': fake_request})
                if serializer.is_valid():
                    serializer.save(created_by=user.active_tenant)
                    created_count += 1
                else:
                    errors.append({'row': index, 'errors': serializer.errors})

    if errors:
        logger.warning(
            'Bulk upload completed with validation errors. created=%s updated=%s skipped=%s failed=%s sample_errors=%s',
            created_count,
            updated_count,
            skipped_count,
            len(errors),
            errors[:5],
        )

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "failed_count": len(errors),
        "errors": errors
    }

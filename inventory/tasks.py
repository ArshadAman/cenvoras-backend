import csv
import logging
from io import StringIO
from celery import shared_task
from django.db import transaction
from inventory.serializers import ProductSerializer
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)

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
    }

    expected_fields = ['name', 'hsn_sac_code', 'description', 'tax', 'stock', 'unit', 'secondary_unit', 'conversion_factor', 'cost_price', 'sale_price', 'low_stock_alert', 'warranty_months']
    optional_nullable_fields = {'hsn_sac_code', 'description', 'secondary_unit', 'sale_price'}
    integer_fields = {'stock', 'conversion_factor', 'low_stock_alert', 'warranty_months'}
    decimal_fields = {'tax', 'cost_price', 'sale_price'}

    unit_aliases = {
        'nos': 'pcs',
        'no': 'pcs',
        'piece': 'pcs',
        'pieces': 'pcs',
        'pc': 'pcs',
        'pcs': 'pcs',
        'unit': 'pcs',
        'units': 'pcs',
        'ltr': 'l',
        'litre': 'l',
        'liter': 'l',
        'litres': 'l',
        'liters': 'l',
    }

    created_count = 0
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
                    continue

                if field == 'unit' and isinstance(value, str):
                    normalized_unit = value.strip().lower()
                    value = unit_aliases.get(normalized_unit, normalized_unit)

                if field in integer_fields:
                    if isinstance(value, str):
                        value = value.replace(',', '').strip()
                    try:
                        value = int(float(value))
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid integer value.']}})
                        payload = None
                        break

                if field in decimal_fields:
                    if isinstance(value, str):
                        value = value.replace(',', '').strip()
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid number value.']}})
                        payload = None
                        break

                payload[field] = value

            if payload is None:
                continue

            serializer = ProductSerializer(data=payload, context={'request': fake_request})
            if serializer.is_valid():
                serializer.save(created_by=user.active_tenant)
                created_count += 1
            else:
                errors.append({'row': index, 'errors': serializer.errors})

    if errors:
        logger.warning(
            'Bulk upload completed with validation errors. created=%s failed=%s sample_errors=%s',
            created_count,
            len(errors),
            errors[:5],
        )

    return {"created_count": created_count, "failed_count": len(errors), "errors": errors}

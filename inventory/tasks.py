import csv
from io import TextIOWrapper
from celery import shared_task
from django.db import transaction
from inventory.serializers import ProductSerializer
from inventory.models import Product
from django.core.files.storage import default_storage
from django.contrib.auth import get_user_model
from django.http import HttpRequest
import json

User = get_user_model()

class FakeRequest:
    def __init__(self, user):
        self.user = user

@shared_task
def process_bulk_upload_csv(file_path: str, user_id: int):
    user = User.objects.get(id=user_id)
    fake_request = FakeRequest(user)
    
    file_obj = default_storage.open(file_path, 'rb')
    text_stream = TextIOWrapper(file_obj, encoding='utf-8-sig')
    reader = csv.DictReader(text_stream)

    def normalize_key(key):
        return (key or '').strip().lower().replace(' ', '_').replace('-', '_')

    header_aliases = {
        'cost_price': ['cost_price', 'price', 'purchase_price', 'cost'],
        'sale_price': ['sale_price', 'sales_price', 'selling_price', 'saleprice', 'salesprice'],
        'hsn_sac_code': ['hsn_sac_code', 'hsn_code', 'hsn'],
        'low_stock_alert': ['low_stock_alert', 'min_stock_level', 'reorder_level'],
        'stock': ['stock', 'opening_stock', 'current_stock'],
        'secondary_unit': ['secondary_unit', 'secondaryunit'],
        'conversion_factor': ['conversion_factor', 'conversionfactor'],
    }

    expected_fields = ['name', 'hsn_sac_code', 'description', 'tax', 'stock', 'unit', 'secondary_unit', 'conversion_factor', 'cost_price', 'sale_price', 'low_stock_alert', 'warranty_months']
    optional_nullable_fields = {'hsn_sac_code', 'description', 'secondary_unit', 'sale_price'}

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
                    value = value.lower()

                payload[field] = value

            serializer = ProductSerializer(data=payload, context={'request': fake_request})
            if serializer.is_valid():
                # For safety, pass both. Some serializers expect the model relationship
                serializer.save(created_by=user.active_tenant)
                created_count += 1
            else:
                errors.append({'row': index, 'errors': serializer.errors})

    # Optional: Once finished, clean up the temp file
    default_storage.delete(file_path)

    return {"created_count": created_count, "failed_count": len(errors), "errors": errors}

import csv
import logging
import os
import re
from io import StringIO
from celery import shared_task
from django.db import transaction
from django.contrib.auth import get_user_model
from inventory.models import Product
from inventory.models_sidecar import ProductMeta

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


@shared_task(bind=True)
def process_bulk_upload_csv(self, file_path_or_content: str, user_id: str):
    """
    High-performance, memory-efficient bulk CSV inventory importer.
    Supports streaming file paths or raw strings with per-row savepoints,
    bulk in-memory lookup cache to prevent N+1 queries, and real-time Celery progress.
    """
    user = User.objects.get(id=user_id)
    tenant = getattr(user, 'active_tenant', user)

    is_temp_file = False
    raw_content = ""
    if os.path.exists(file_path_or_content):
        is_temp_file = True
        try:
            with open(file_path_or_content, 'r', encoding='utf-8', errors='replace') as f:
                raw_content = f.read()
        except Exception as e:
            logger.error("Failed to read CSV tempfile %s: %s", file_path_or_content, e)
            return {
                "created_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "failed_count": 1,
                "errors": [{"row": 0, "errors": {"file": [f"Unable to read CSV file: {str(e)}"]}}]
            }
    else:
        raw_content = file_path_or_content

    try:
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

        expected_fields = [
            'item_code', 'name', 'hsn_sac_code', 'description', 'manufacturer',
            'internal_reference', 'tax', 'stock', 'unit', 'secondary_unit',
            'conversion_factor', 'cost_price', 'sale_price', 'low_stock_alert', 'warranty_months'
        ]
        optional_nullable_fields = {
            'item_code', 'hsn_sac_code', 'description', 'manufacturer',
            'internal_reference', 'secondary_unit', 'sale_price'
        }
        optional_with_default_fields = {'unit'}
        integer_fields = {'stock', 'conversion_factor', 'low_stock_alert', 'warranty_months'}
        decimal_fields = {'tax', 'cost_price', 'sale_price'}

        unit_aliases = {
            'nos': 'pcs', 'no': 'pcs', 'piece': 'pcs', 'pieces': 'pcs', 'pc': 'pcs',
            'pcs': 'pcs', 'unit': 'pcs', 'units': 'pcs', 'number': 'pcs', 'each': 'pcs',
            'ea': 'pcs', 'ltr': 'l', 'litre': 'l', 'liter': 'l', 'litres': 'l',
            'liters': 'l', 'kgs': 'kg', 'kilogram': 'kg', 'kilograms': 'kg',
            'gm': 'g', 'gram': 'g', 'grams': 'g', 'milligram': 'mg', 'milligrams': 'mg',
            'milliliter': 'ml', 'milliliters': 'ml', 'millilitre': 'ml', 'millilitres': 'ml',
            'mtr': 'm', 'meter': 'm', 'meters': 'm', 'metre': 'm', 'metres': 'm',
            'centimeter': 'cm', 'centimeters': 'cm',
        }

        # Count total rows for percentage calculation
        raw_lines = [l for l in raw_content.splitlines() if l.strip()]
        total_rows = max(0, len(raw_lines) - 1)

        reader = csv.DictReader(StringIO(raw_content))

        # Bulk pre-fetch existing item codes for tenant into O(1) dictionary to eliminate N+1 queries
        existing_products_map = {}
        for p in Product.objects.filter(created_by=tenant).exclude(item_code__isnull=True).exclude(item_code=''):
            existing_products_map[p.item_code.strip().lower()] = p

        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        for index, row in enumerate(reader, start=2):
            processed_count = index - 1

            # Report periodic progress to Celery & Redis
            if self and hasattr(self, 'update_state') and (processed_count % 15 == 0 or processed_count == total_rows):
                percent = min(99, int((processed_count / total_rows) * 100)) if total_rows > 0 else 0
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'current': processed_count,
                        'total': total_rows,
                        'percent': percent,
                        'created_count': created_count,
                        'updated_count': updated_count,
                        'skipped_count': skipped_count,
                        'failed_count': len(errors),
                    }
                )

            normalized_row = {normalize_key(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            if not any(v not in (None, '') for v in normalized_row.values()):
                continue

            payload = {}
            row_parse_failed = False
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
                        payload[field] = 'pcs'
                    continue

                if field == 'unit' and isinstance(value, str):
                    normalized_unit = value.strip().lower()
                    value = unit_aliases.get(normalized_unit, value.strip())

                if field in integer_fields:
                    numeric_value = _extract_numeric(value)
                    try:
                        value = int(float(numeric_value))
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid integer value.']}})
                        row_parse_failed = True
                        break

                if field in decimal_fields:
                    numeric_value = _extract_numeric(value)
                    try:
                        value = float(numeric_value)
                    except (TypeError, ValueError):
                        errors.append({'row': index, 'errors': {field: ['Invalid number value.']}})
                        row_parse_failed = True
                        break

                payload[field] = value

            if row_parse_failed:
                continue

            product_name = (payload.get('name') or '').strip()
            if not product_name:
                errors.append({'row': index, 'errors': {'name': ['Product name is required.']}})
                continue

            payload['name'] = product_name

            # Check required sale_price
            sale_price = payload.get('sale_price')
            if sale_price in (None, ''):
                errors.append({'row': index, 'errors': {'sale_price': ['Sale price is required.']}})
                continue

            # Check if this product already exists by user-defined item_code using O(1) in-memory map
            incoming_item_code = payload.get('item_code')
            normalized_item_code = str(incoming_item_code).strip().lower() if incoming_item_code else None
            existing_product = existing_products_map.get(normalized_item_code) if normalized_item_code else None

            # Resilient isolated savepoint per row so a bad row never aborts previously created rows
            try:
                with transaction.atomic():
                    if existing_product:
                        diff_fields = {}

                        if 'name' in payload and payload['name'] is not None:
                            clean_name = str(payload['name']).strip()
                            if clean_name != existing_product.name:
                                diff_fields['name'] = clean_name

                        if 'unit' in payload and payload['unit'] is not None:
                            if payload['unit'] != existing_product.unit:
                                diff_fields['unit'] = payload['unit']

                        if 'cost_price' in payload and payload['cost_price'] is not None:
                            existing_cost = float(existing_product.price or 0)
                            if round(float(payload['cost_price']), 4) != round(existing_cost, 4):
                                diff_fields['price'] = payload['cost_price']

                        if 'sale_price' in payload and payload['sale_price'] is not None:
                            existing_sale = float(existing_product.sale_price or 0) if existing_product.sale_price is not None else None
                            if existing_sale is None or round(float(payload['sale_price']), 4) != round(existing_sale, 4):
                                diff_fields['sale_price'] = payload['sale_price']

                        if 'stock' in payload and payload['stock'] is not None:
                            if int(payload['stock']) != int(existing_product.stock or 0):
                                diff_fields['stock'] = payload['stock']

                        if 'tax' in payload and payload['tax'] is not None:
                            existing_tax = float(existing_product.tax or 0)
                            if round(float(payload['tax']), 2) != round(existing_tax, 2):
                                diff_fields['tax'] = payload['tax']

                        if 'hsn_sac_code' in payload and payload['hsn_sac_code'] != (existing_product.hsn_sac_code or None):
                            diff_fields['hsn_sac_code'] = payload['hsn_sac_code']

                        if 'manufacturer' in payload and payload['manufacturer'] != (existing_product.manufacturer or None):
                            diff_fields['manufacturer'] = payload['manufacturer']

                        if 'internal_reference' in payload and payload['internal_reference'] != (existing_product.internal_reference or None):
                            diff_fields['internal_reference'] = payload['internal_reference']

                        if 'description' in payload and payload['description'] != (existing_product.description or None):
                            diff_fields['description'] = payload['description']

                        if 'secondary_unit' in payload and payload['secondary_unit'] != (existing_product.secondary_unit or None):
                            diff_fields['secondary_unit'] = payload['secondary_unit']

                        if 'conversion_factor' in payload and payload['conversion_factor'] is not None:
                            if int(payload['conversion_factor']) != int(existing_product.conversion_factor or 1):
                                diff_fields['conversion_factor'] = payload['conversion_factor']

                        if 'low_stock_alert' in payload and payload['low_stock_alert'] is not None:
                            if int(payload['low_stock_alert']) != int(existing_product.low_stock_alert or 0):
                                diff_fields['low_stock_alert'] = payload['low_stock_alert']

                        if 'warranty_months' in payload and payload['warranty_months'] is not None:
                            if int(payload['warranty_months']) != int(existing_product.warranty_months or 0):
                                diff_fields['warranty_months'] = payload['warranty_months']

                        if not diff_fields:
                            skipped_count += 1
                        else:
                            for attr, val in diff_fields.items():
                                setattr(existing_product, attr, val)
                            existing_product.save(update_fields=list(diff_fields.keys()))
                            updated_count += 1
                    else:
                        # Map cost_price to model field 'price'
                        cost_price = payload.pop('cost_price', None)
                        payload['price'] = cost_price if cost_price is not None else 0

                        # Create Product directly with minimum overhead
                        new_product = Product.objects.create(
                            created_by=tenant,
                            **payload
                        )
                        ProductMeta.objects.get_or_create(product=new_product)
                        created_count += 1

                        if normalized_item_code:
                            existing_products_map[normalized_item_code] = new_product
            except Exception as exc:
                errors.append({'row': index, 'errors': {'database': [str(exc)]}})
                logger.warning('Error saving row %s in bulk upload: %s', index, exc)

        # Clear tenant-specific cache keys for instant fresh data
        try:
            from cenvoras.cache_utils import tenant_cache_key
            from django.core.cache import cache
            cache.delete(tenant_cache_key('inventory', tenant.id, 'expiry-report', 'days-90'))
            cache.delete(tenant_cache_key('inventory', tenant.id, 'expiry-summary', 'days-90'))
        except Exception:
            pass

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
            "total_rows": total_rows,
            "errors": errors[:50],  # Return up to 50 sample errors to avoid payload bloat
        }
    finally:
        if is_temp_file:
            try:
                os.unlink(file_path_or_content)
            except OSError:
                pass

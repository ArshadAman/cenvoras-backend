import csv
import io
import json
import logging
import os
import tempfile
from decimal import Decimal

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q

from .models import Customer, SalesInvoice, SalesInvoiceItem
from .serializers import SalesInvoiceSerializer
from inventory.models import Product


logger = logging.getLogger(__name__)
User = get_user_model()


def _apply_sales_export_filters(queryset, filters):
    search = (filters.get('search') or '').strip()
    if search:
        queryset = queryset.filter(
            Q(invoice_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
        )

    status_filter = (filters.get('status') or '').strip()
    if status_filter and status_filter != 'all':
        queryset = queryset.filter(status=status_filter)

    customer_filter = (filters.get('customer') or '').strip()
    if customer_filter:
        queryset = queryset.filter(
            Q(customer_name__icontains=customer_filter)
            | Q(customer__name__icontains=customer_filter)
        )

    date_start = (filters.get('date_start') or '').strip() or (filters.get('invoice_date_after') or '').strip()
    date_end = (filters.get('date_end') or '').strip() or (filters.get('invoice_date_before') or '').strip()
    if date_start:
        queryset = queryset.filter(invoice_date__gte=date_start)
    if date_end:
        queryset = queryset.filter(invoice_date__lte=date_end)

    amount_min = (filters.get('amount_min') or '').strip()
    amount_max = (filters.get('amount_max') or '').strip()
    if amount_min:
        queryset = queryset.filter(total_amount__gte=amount_min)
    if amount_max:
        queryset = queryset.filter(total_amount__lte=amount_max)

    has_overdue = str(filters.get('has_overdue') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if has_overdue:
        from django.utils import timezone
        today = timezone.localdate()
        queryset = queryset.filter(
            Q(due_date__lt=today) | (Q(due_date__isnull=True) & Q(invoice_date__lt=today))
        )

    selected_ids_param = (filters.get('selected_ids') or '').strip()
    if selected_ids_param:
        selected_ids = [value.strip() for value in selected_ids_param.split(',') if value.strip()]
        if selected_ids:
            queryset = queryset.filter(id__in=selected_ids)

    ordering = (filters.get('ordering') or '-invoice_date').strip()
    allowed_ordering = {
        'invoice_date', '-invoice_date',
        'created_at', '-created_at',
        'total_amount', '-total_amount',
        'invoice_number', '-invoice_number',
        'customer_name', '-customer_name',
    }
    if ordering not in allowed_ordering:
        ordering = '-invoice_date'

    return queryset.order_by(ordering, '-created_at')


def _jsonify(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


def _build_sales_export_file(user_id, filters):
    user = User.objects.get(id=user_id)
    tenant = getattr(user, 'active_tenant', user)
    invoices = SalesInvoice.objects.filter(
        created_by=tenant
    ).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')

    invoices = _apply_sales_export_filters(invoices, filters or {})
    invoice_rows = list(SalesInvoiceSerializer(invoices, many=True).data)

    headers = [
        'invoice_row_type',
        'invoice_index',
        'item_index',
        'items_count',
        'invoice_id',
        'invoice_number',
        'invoice_date',
        'due_date',
        'po_number',
        'po_date',
        'challan_number',
        'challan_date',
        'delivery_address',
        'place_of_supply',
        'gst_treatment',
        'journal',
        'warehouse',
        'status',
        'total_amount',
        'amount_paid',
        'payment_status',
        'round_off',
        'created_by',
        'created_at',
        'customer_name',
        'customer_email',
        'customer_phone',
        'customer_address',
        'customer_details_json',
        'invoice_payload_json',
        'item_id',
        'item_product',
        'item_product_detail_json',
        'item_hsn_sac_code',
        'item_unit',
        'item_quantity',
        'item_free_quantity',
        'item_price',
        'item_discount',
        'item_tax',
        'item_amount',
        'item_batch_json',
        'product_id',
        'product_name',
        'product_description',
        'product_hsn_sac_code',
        'product_unit',
        'batch_id',
        'batch_number',
        'batch_expiry_date',
        'batch_manufacturing_date',
        'batch_mrp',
        'batch_cost_price',
        'batch_sale_price',
        'batch_notes',
        'item_payload_json',
        'batch_payload_json',
        'meta_json',
    ]

    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', mode='w', encoding='utf-8', newline='') as temp_file:
        writer = csv.DictWriter(temp_file, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()

        for invoice_index, serialized_invoice in enumerate(invoice_rows, start=1):
            invoice_model = invoices[invoice_index - 1]
            invoice_payload = dict(serialized_invoice)
            customer_details = invoice_payload.pop('customer_details', None)
            serialized_items = invoice_payload.pop('items', []) or []

            base_row = {
                'invoice_row_type': 'invoice',
                'invoice_index': invoice_index,
                'item_index': '',
                'items_count': len(serialized_items),
                'invoice_id': invoice_payload.get('id', ''),
                'invoice_number': invoice_payload.get('invoice_number', ''),
                'invoice_date': invoice_payload.get('invoice_date', ''),
                'due_date': invoice_payload.get('due_date', ''),
                'po_number': invoice_payload.get('po_number', ''),
                'po_date': invoice_payload.get('po_date', ''),
                'challan_number': invoice_payload.get('challan_number', ''),
                'challan_date': invoice_payload.get('challan_date', ''),
                'delivery_address': invoice_payload.get('delivery_address', ''),
                'place_of_supply': invoice_payload.get('place_of_supply', ''),
                'gst_treatment': invoice_payload.get('gst_treatment', ''),
                'journal': invoice_payload.get('journal', ''),
                'warehouse': invoice_payload.get('warehouse', ''),
                'status': invoice_payload.get('status', ''),
                'total_amount': invoice_payload.get('total_amount', ''),
                'amount_paid': invoice_payload.get('amount_paid', ''),
                'payment_status': invoice_payload.get('payment_status', ''),
                'round_off': invoice_payload.get('round_off', ''),
                'created_by': invoice_payload.get('created_by', ''),
                'created_at': invoice_payload.get('created_at', ''),
                'customer_name': invoice_payload.get('customer_name', ''),
                'customer_email': invoice_payload.get('customer_email', ''),
                'customer_phone': invoice_payload.get('customer_phone', ''),
                'customer_address': invoice_payload.get('customer_address', ''),
                'customer_details_json': customer_details,
                'invoice_payload_json': invoice_payload,
                'meta_json': invoice_payload.get('meta', ''),
            }

            if not serialized_items:
                empty_row = dict(base_row)
                empty_row.update({
                    'item_id': '', 'item_product': '', 'item_product_detail_json': '', 'item_hsn_sac_code': '',
                    'item_unit': '', 'item_quantity': '', 'item_free_quantity': '', 'item_price': '',
                    'item_discount': '', 'item_tax': '', 'item_amount': '', 'item_batch_json': '',
                    'product_id': '', 'product_name': '', 'product_description': '', 'product_hsn_sac_code': '',
                    'product_unit': '', 'batch_id': '', 'batch_number': '', 'batch_expiry_date': '',
                    'batch_manufacturing_date': '', 'batch_mrp': '', 'batch_cost_price': '',
                    'batch_sale_price': '', 'batch_notes': '', 'item_payload_json': '', 'batch_payload_json': '',
                })
                writer.writerow({key: _jsonify(value) for key, value in empty_row.items()})
                continue

            invoice_items = list(invoice_model.items.all())
            for item_index, serialized_item in enumerate(serialized_items, start=1):
                model_item = invoice_items[item_index - 1] if item_index - 1 < len(invoice_items) else None
                product_detail = serialized_item.get('product_detail') or {}
                batch_model = getattr(model_item, 'batch', None)
                batch_payload = {
                    'id': str(getattr(batch_model, 'id', '') or '') if batch_model else '',
                    'batch_number': getattr(batch_model, 'batch_number', '') if batch_model else '',
                    'expiry_date': getattr(batch_model, 'expiry_date', '') if batch_model else '',
                    'manufacturing_date': getattr(batch_model, 'manufacturing_date', '') if batch_model else '',
                    'mrp': getattr(batch_model, 'mrp', '') if batch_model else '',
                    'cost_price': getattr(batch_model, 'cost_price', '') if batch_model else '',
                    'sale_price': getattr(batch_model, 'sale_price', '') if batch_model else '',
                    'notes': getattr(batch_model, 'notes', '') if batch_model else '',
                }

                row = dict(base_row)
                row.update({
                    'invoice_row_type': 'item',
                    'item_index': item_index,
                    'item_id': serialized_item.get('id', ''),
                    'item_product': serialized_item.get('product', ''),
                    'item_product_detail_json': product_detail,
                    'item_hsn_sac_code': serialized_item.get('hsn_sac_code', ''),
                    'item_unit': serialized_item.get('unit', ''),
                    'item_quantity': serialized_item.get('quantity', ''),
                    'item_free_quantity': serialized_item.get('free_quantity', ''),
                    'item_price': serialized_item.get('price', ''),
                    'item_discount': serialized_item.get('discount', ''),
                    'item_tax': serialized_item.get('tax', ''),
                    'item_amount': serialized_item.get('amount', ''),
                    'item_batch_json': serialized_item.get('batch', ''),
                    'product_id': product_detail.get('id', ''),
                    'product_name': product_detail.get('name', ''),
                    'product_description': product_detail.get('description', ''),
                    'product_hsn_sac_code': product_detail.get('hsn_sac_code', ''),
                    'product_unit': product_detail.get('unit', ''),
                    'batch_id': batch_payload.get('id', ''),
                    'batch_number': batch_payload.get('batch_number', ''),
                    'batch_expiry_date': batch_payload.get('expiry_date', ''),
                    'batch_manufacturing_date': batch_payload.get('manufacturing_date', ''),
                    'batch_mrp': batch_payload.get('mrp', ''),
                    'batch_cost_price': batch_payload.get('cost_price', ''),
                    'batch_sale_price': batch_payload.get('sale_price', ''),
                    'batch_notes': batch_payload.get('notes', ''),
                    'item_payload_json': serialized_item,
                    'batch_payload_json': batch_payload,
                })
                writer.writerow({key: _jsonify(value) for key, value in row.items()})

    filename = f'sales-invoices-{user_id}.csv'
    return {
        'filename': filename,
        'file_path': temp_file.name,
        'rows': len(invoice_rows),
    }


@shared_task
def process_sales_invoice_csv(csv_source: str, user_id: str):
    user = User.objects.get(id=user_id)
    tenant = getattr(user, 'active_tenant', user)
    if os.path.exists(csv_source):
        with open(csv_source, 'rb') as source_file:
            csv_content = source_file.read().decode('utf-8-sig')
        try:
            os.remove(csv_source)
        except OSError:
            pass
    else:
        csv_content = csv_source

    reader = csv.DictReader(io.StringIO(csv_content))
    required_headers = {'bill_number', 'sale_date', 'customer_name', 'product_name', 'quantity', 'price'}

    if not reader.fieldnames or not required_headers.issubset(set(h.strip() for h in reader.fieldnames if h)):
        raise ValueError('Invalid CSV template. Required columns: bill_number, sale_date, customer_name, product_name, quantity, price.')

    created_count = 0
    failed_count = 0
    errors = []

    with transaction.atomic():
        for index, row in enumerate(reader, start=2):
            if not any((value or '').strip() for value in row.values()):
                continue

            bill_number = (row.get('bill_number') or '').strip()
            sale_date = (row.get('sale_date') or '').strip()
            customer_name = (row.get('customer_name') or '').strip()
            customer_address = (row.get('customer_address') or '').strip()
            customer_gstin = (row.get('customer_gstin') or '').strip()
            due_date = (row.get('due_date') or '').strip() or None
            product_name = (row.get('product_name') or '').strip()

            try:
                quantity = int(float(row.get('quantity') or 0))
                unit = (row.get('unit') or 'pcs').strip() or 'pcs'
                price = Decimal(str(row.get('price') or '0'))
                discount = Decimal(str(row.get('discount') or '0'))
                tax = Decimal(str(row.get('tax') or '0'))
                hsn_code = (row.get('hsn_code') or '').strip()

                if not (bill_number and sale_date and customer_name and product_name and quantity > 0):
                    raise ValueError('Each row must include bill_number, sale_date, customer_name, product_name, and a quantity greater than 0.')

                customer, _ = Customer.objects.get_or_create(
                    created_by=tenant,
                    name=customer_name,
                    defaults={
                        'address': customer_address or None,
                        'gstin': customer_gstin or None,
                    }
                )
                if customer_address and customer.address != customer_address:
                    customer.address = customer_address
                if customer_gstin and customer.gstin != customer_gstin:
                    customer.gstin = customer_gstin
                customer.save()

                product, _ = Product.objects.get_or_create(
                    created_by=tenant,
                    name=product_name,
                    defaults={
                        'unit': unit,
                        'hsn_sac_code': hsn_code or None,
                        'price': price,
                        'tax': tax,
                    }
                )

                if SalesInvoice.objects.filter(created_by=tenant, invoice_number=bill_number).exists():
                    continue

                invoice = SalesInvoice.objects.create(
                    created_by=tenant,
                    customer=customer,
                    customer_name=customer_name,
                    customer_address=customer_address or customer.address,
                    invoice_number=bill_number,
                    invoice_date=sale_date,
                    due_date=due_date,
                    total_amount=Decimal('0'),
                    journal='Sales',
                    status='final',
                )

                base_amount = Decimal(str(quantity)) * price
                discount_amount = (base_amount * discount) / Decimal('100')
                taxable_amount = base_amount - discount_amount
                tax_amount = (taxable_amount * tax) / Decimal('100')
                amount = (taxable_amount + tax_amount).quantize(Decimal('0.01'))

                SalesInvoiceItem.objects.create(
                    sales_invoice=invoice,
                    product=product,
                    quantity=quantity,
                    price=price,
                    discount=discount,
                    tax=tax,
                    amount=amount,
                    unit=unit,
                    hsn_sac_code=hsn_code or None,
                )

                invoice.total_amount = amount
                invoice.save(update_fields=['total_amount'])
                created_count += 1
            except Exception as exc:
                failed_count += 1
                errors.append({'row': index, 'error': str(exc)})

    return {
        'created_count': created_count,
        'failed_count': failed_count,
        'errors': errors,
    }


@shared_task
def generate_sales_invoice_csv(user_id: str, filters: dict):
    return _build_sales_export_file(user_id, filters)
from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import PurchaseBill, SalesInvoice, PurchaseBillItem
from .serializers import PurchaseBillSerializer, SalesInvoiceSerializer
from inventory.serializers import ProductSerializer
from .filters import PurchaseBillFilter, SalesInvoiceFilter
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from decimal import Decimal
from django.db.models import Sum
from django.db import DatabaseError, ProgrammingError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant
    ).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')

    search = request.GET.get('search', '').strip()
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
        )

    status_filter = request.GET.get('status', '').strip()
    if status_filter and status_filter != 'all':
        invoices = invoices.filter(status=status_filter)

    customer_filter = request.GET.get('customer', '').strip()
    if customer_filter:
        invoices = invoices.filter(
            Q(customer_name__icontains=customer_filter)
            | Q(customer__name__icontains=customer_filter)
        )

    date_start = request.GET.get('date_start', '').strip() or request.GET.get('invoice_date_after', '').strip()
    date_end = request.GET.get('date_end', '').strip() or request.GET.get('invoice_date_before', '').strip()
    if date_start:
        invoices = invoices.filter(invoice_date__gte=date_start)
    if date_end:
        invoices = invoices.filter(invoice_date__lte=date_end)

    amount_min = request.GET.get('amount_min', '').strip()
    amount_max = request.GET.get('amount_max', '').strip()
    if amount_min:
        invoices = invoices.filter(total_amount__gte=amount_min)
    if amount_max:
        invoices = invoices.filter(total_amount__lte=amount_max)

    has_overdue = request.GET.get('has_overdue', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if has_overdue:
        today = timezone.localdate()
        invoices = invoices.filter(
            Q(due_date__lt=today) | (Q(due_date__isnull=True) & Q(invoice_date__lt=today))
        )

    selected_ids_param = request.GET.get('selected_ids', '').strip()
    if selected_ids_param:
        selected_ids = [value.strip() for value in selected_ids_param.split(',') if value.strip()]
        if selected_ids:
            invoices = invoices.filter(id__in=selected_ids)

    ordering = request.GET.get('ordering', '-invoice_date').strip()
    allowed_ordering = {
        'invoice_date', '-invoice_date',
        'created_at', '-created_at',
        'total_amount', '-total_amount',
        'invoice_number', '-invoice_number',
        'customer_name', '-customer_name',
    }
    if ordering not in allowed_ordering:
        ordering = '-invoice_date'
    invoices = invoices.order_by(ordering, '-created_at')

    invoice_data = list(SalesInvoiceSerializer(invoices, many=True).data)

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

    def stringify(value):
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str, ensure_ascii=False)
        return value

    rows = []
    for invoice_index, serialized_invoice in enumerate(invoice_data, start=1):
        invoice = invoices[invoice_index - 1]
        invoice_payload = dict(serialized_invoice)
        customer_details = invoice_payload.pop('customer_details', None)
        serialized_items = invoice_payload.pop('items', []) or []

        base_invoice = {
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
            row = dict(base_invoice)
            row.update({
                'item_id': '',
                'item_product': '',
                'item_product_detail_json': '',
                'item_hsn_sac_code': '',
                'item_unit': '',
                'item_quantity': '',
                'item_free_quantity': '',
                'item_price': '',
                'item_discount': '',
                'item_tax': '',
                'item_amount': '',
                'item_batch_json': '',
                'product_id': '',
                'product_name': '',
                'product_description': '',
                'product_hsn_sac_code': '',
                'product_unit': '',
                'batch_id': '',
                'batch_number': '',
                'batch_expiry_date': '',
                'batch_manufacturing_date': '',
                'batch_mrp': '',
                'batch_cost_price': '',
                'batch_sale_price': '',
                'batch_notes': '',
                'item_payload_json': '',
                'batch_payload_json': '',
            })
            rows.append({key: stringify(value) for key, value in row.items()})
            continue

        for item_index, serialized_item in enumerate(serialized_items, start=1):
            product_detail = serialized_item.get('product_detail') or {}
            batch_model = invoice.items.all()[item_index - 1].batch if invoice.items.all()[item_index - 1].batch_id else None
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
            row = dict(base_invoice)
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
                'batch_payload_json': batch_payload,
                'item_payload_json': serialized_item,
            })
            rows.append({key: stringify(value) for key, value in row.items()})

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="sales-invoices-{timezone.localdate()}.csv"'
    return response
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            print("DEBUG: Serializer validation failed")
            try:
                error_details = serializer.errors
                print("DEBUG: Serializer errors:", error_details)
            except Exception as e:
                print("DEBUG: Error accessing serializer.errors:", str(e))
                error_details = {'general': ['Validation failed - unable to retrieve detailed errors']}
            
            return Response({
                'error': 'Validation failed',
                'details': error_details
            }, status=status.HTTP_400_BAD_REQUEST)
    

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Sales invoice detail",
        examples={
            "application/json": {
                "id": "uuid-2",
                "invoice_number": "INV-001",
                "invoice_date": "2025-08-01",
                "customer": "uuid-cust-1",
                "total_amount": 15000,
                "created_by": 1,
                "created_at": "2025-08-01T11:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-2",
                        "quantity": 5,
                        "unit": "pcs",
                        "price": 3000,
                        "amount": 15000
                    }
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_invoice_detail(request, pk):
    try:
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user.active_tenant)
    except SalesInvoice.DoesNotExist:
        return Response({"success": False, "message": "Not found."}, status=404)
    serializer = SalesInvoiceSerializer(invoice)
    return Response({"success": True, "data": serializer.data})

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=PurchaseBillSerializer,
    responses={200: openapi.Response(
        description="Updated purchase bill",
        examples={
            "application/json": {
                "id": "uuid-1",
                "bill_number": "PB-001",
                "bill_date": "2025-08-01",
                "vendor_name": "Vendor A",
                "total_amount": 10000,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-1",
                        "quantity": 10,
                        "unit": "pcs",
                        "price": 1000,
                        "amount": 10000
                    }
                ]
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def purchase_bill_update_delete(request, pk):
    try:
        bill = PurchaseBill.objects.get(pk=pk, created_by=request.user.active_tenant)
    except PurchaseBill.DoesNotExist:
        return Response({
            "success": False,
            "message": "Purchase bill not found."
        }, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = PurchaseBillSerializer(bill, data=request.data, partial=(request.method == 'PATCH'), context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Purchase bill updated successfully.",
                "data": serializer.data
            })
        return Response({
            "success": False,
            "message": "Validation error.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if bill.payment_status != 'pending':
            return Response({
                "success": False,
                "message": "Only pending purchase bills can be deleted."
            }, status=status.HTTP_400_BAD_REQUEST)
        bill.delete()
        return Response({
            "success": True,
            "message": "Purchase bill deleted successfully."
        }, status=status.HTTP_204_NO_CONTENT)

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=SalesInvoiceSerializer,
    responses={200: openapi.Response(
        description="Updated sales invoice",
        examples={
            "application/json": {
                "id": "uuid-2",
                "invoice_number": "INV-001",
                "invoice_date": "2025-08-01",
                "customer": "uuid-cust-1",
                "total_amount": 15000,
                "created_by": 1,
                "created_at": "2025-08-01T11:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-2",
                        "quantity": 5,
                        "unit": "pcs",
                        "price": 3000,
                        "amount": 15000
                    }
                ]
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_invoice_update_delete(request, pk):
    try:
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user.active_tenant)
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = SalesInvoiceSerializer(
            invoice, 
            data=request.data, 
            partial=(request.method == 'PATCH'),
            context={'request': request}  # Add this line
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if invoice.payment_status != 'pending':
            return Response({
                'error': 'Only pending sales invoices can be deleted.'
            }, status=status.HTTP_400_BAD_REQUEST)
        invoice.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_sales_invoices_csv(request):
    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant
    ).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')

    search = request.GET.get('search', '').strip()
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
        )

    status_filter = request.GET.get('status', '').strip()
    if status_filter and status_filter != 'all':
        invoices = invoices.filter(status=status_filter)

    customer_filter = request.GET.get('customer', '').strip()
    if customer_filter:
        invoices = invoices.filter(
            Q(customer_name__icontains=customer_filter)
            | Q(customer__name__icontains=customer_filter)
        )

    date_start = request.GET.get('date_start', '').strip() or request.GET.get('invoice_date_after', '').strip()
    date_end = request.GET.get('date_end', '').strip() or request.GET.get('invoice_date_before', '').strip()
    if date_start:
        invoices = invoices.filter(invoice_date__gte=date_start)
    if date_end:
        invoices = invoices.filter(invoice_date__lte=date_end)

    amount_min = request.GET.get('amount_min', '').strip()
    amount_max = request.GET.get('amount_max', '').strip()
    if amount_min:
        invoices = invoices.filter(total_amount__gte=amount_min)
    if amount_max:
        invoices = invoices.filter(total_amount__lte=amount_max)

    has_overdue = request.GET.get('has_overdue', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if has_overdue:
        today = timezone.localdate()
        invoices = invoices.filter(
            Q(due_date__lt=today) | (Q(due_date__isnull=True) & Q(invoice_date__lt=today))
        )

    selected_ids_param = request.GET.get('selected_ids', '').strip()
    if selected_ids_param:
        selected_ids = [value.strip() for value in selected_ids_param.split(',') if value.strip()]
        if selected_ids:
            invoices = invoices.filter(id__in=selected_ids)

    ordering = request.GET.get('ordering', '-invoice_date').strip()
    allowed_ordering = {
        'invoice_date', '-invoice_date',
        'created_at', '-created_at',
        'total_amount', '-total_amount',
        'invoice_number', '-invoice_number',
        'customer_name', '-customer_name',
    }
    if ordering not in allowed_ordering:
        ordering = '-invoice_date'
    invoices = invoices.order_by(ordering, '-created_at')

    invoice_data = SalesInvoiceSerializer(invoices, many=True).data

    headers = [
        'invoice_id',
        'invoice_number',
        'invoice_date',
        'due_date',
        'po_number',
    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant,
        invoice_number__startswith=full_prefix
    )
    
    max_num = 0
    for inv in invoices:
        suffix = inv.invoice_number.replace(full_prefix, '')
        try:
            num = int(suffix)
            if num > max_num:
                max_num = num
        except ValueError:
            pass
            
    next_num = max_num + 1
    next_invoice = f"{full_prefix}{next_num:03d}"
    return Response({
        "success": True,
        "uuid_prefix": tenant_id,
        "next_number": next_invoice,
        "suffix": f"{next_num:03d}"
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_summary_analytics(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    qs = SalesInvoice.objects.filter(created_by=request.user.active_tenant)
    if start_date:
        qs = qs.filter(invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice_date__lte=end_date)
        
    total_revenue = qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    total_invoices = qs.count()
    
    now = timezone.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    this_month_qs = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant,
        invoice_date__gte=this_month_start.date()
    )
    this_month_revenue = this_month_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    this_month_invoices = this_month_qs.count()
    
    return Response({
        "success": True,
        "total_revenue": total_revenue,
        "total_invoices": total_invoices,
        "this_month_revenue": this_month_revenue,
        "this_month_invoices": this_month_invoices,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recalculate_invoice_totals(request):
    """
    Recalculate invoice totals from items for all invoices (for fixing data inconsistencies).
    """
    invoices = SalesInvoice.objects.filter(created_by=request.user.active_tenant)
    fixed_count = 0
    
    for invoice in invoices:
        items_total = sum(
            Decimal(str(item.amount or 0)) 
            for item in invoice.items.all()
        )
        
        if invoice.total_amount != items_total:
            invoice.total_amount = items_total
            invoice.save(update_fields=['total_amount'])
            fixed_count += 1
    
    return Response({
        "success": True,
        "message": f"Fixed {fixed_count} invoices with incorrect totals"
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_sales_invoices_csv(request):
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return Response({'error': 'CSV file is required using form key "file".'}, status=status.HTTP_400_BAD_REQUEST)

    if not uploaded_file.name.lower().endswith('.csv'):
        return Response({'error': 'Only CSV files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        raw_content = uploaded_file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return Response({'error': 'Unable to decode CSV. Please upload a UTF-8 encoded file.'}, status=status.HTTP_400_BAD_REQUEST)

    reader = csv.DictReader(io.StringIO(raw_content))
    required_headers = {'bill_number', 'sale_date', 'customer_name', 'product_name', 'quantity', 'price'}
    if not reader.fieldnames or not required_headers.issubset(set(h.strip() for h in reader.fieldnames if h)):
        return Response({
            'error': 'Invalid CSV template.',
            'details': 'Required columns: bill_number, sale_date, customer_name, product_name, quantity, price.'
        }, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    created_count = 0

    from inventory.models import Product
    from decimal import Decimal
    from .models import Customer, SalesInvoice, SalesInvoiceItem

    with transaction.atomic():
        for row in reader:
            if not any((value or '').strip() for value in row.values()):
                continue

            bill_number = (row.get('bill_number') or '').strip()
            sale_date = (row.get('sale_date') or '').strip()
            customer_name = (row.get('customer_name') or '').strip()
            customer_address = (row.get('customer_address') or '').strip()
            customer_gstin = (row.get('customer_gstin') or '').strip()
            due_date = (row.get('due_date') or '').strip() or None
            product_name = (row.get('product_name') or '').strip()
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

    return Response({
        'success': True,
        'created_count': created_count,
        'message': f'Successfully imported {created_count} sales invoices.'
    }, status=status.HTTP_201_CREATED)

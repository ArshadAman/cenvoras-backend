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
import csv
import io
import os
from celery.result import AsyncResult
from django.http import FileResponse
from .tasks import generate_sales_invoice_csv, process_sales_invoice_csv


def _csv_job_status(task_id):
    task = AsyncResult(task_id)
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

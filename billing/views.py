from decimal import Decimal

from django.db import DatabaseError, ProgrammingError
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
import logging

from inventory.serializers import ProductSerializer

from .models import PurchaseBill, PurchaseBillItem, SalesInvoice, PurchaseOrder
from .serializers import PurchaseBillSerializer, SalesInvoiceSerializer
from .serializers_purchase_order import PurchaseOrderSerializer


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def purchase_bill_list_create(request):
    tenant = request.user.active_tenant

    if request.method == 'GET':
        bills = (
            PurchaseBill.objects.filter(created_by=tenant)
            .order_by('-bill_date', '-created_at')
            .prefetch_related('items__product')
        )
        serializer = PurchaseBillSerializer(bills, many=True)
        return Response({'success': True, 'data': serializer.data})

    serializer = PurchaseBillSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        serializer.save(created_by=tenant)
        return Response(
            {'success': True, 'message': 'Purchase bill created successfully.', 'data': serializer.data},
            status=status.HTTP_201_CREATED,
        )
    return Response(
        {'success': False, 'message': 'Validation error.', 'errors': serializer.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def purchase_bill_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        bill = PurchaseBill.objects.select_related('warehouse').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except PurchaseBill.DoesNotExist:
        return Response({'success': False, 'message': 'Purchase bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = PurchaseBillSerializer(bill)
    return Response({'success': True, 'data': serializer.data})


@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def purchase_bill_update_delete(request, pk):
    tenant = request.user.active_tenant
    try:
        bill = PurchaseBill.objects.get(pk=pk, created_by=tenant)
    except PurchaseBill.DoesNotExist:
        return Response({'success': False, 'message': 'Purchase bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        if bill.payment_status != 'pending':
            return Response(
                {'success': False, 'message': 'Only pending purchase bills can be edited.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PurchaseBillSerializer(
            bill,
            data=request.data,
            partial=(request.method == 'PATCH'),
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'message': 'Purchase bill updated successfully.', 'data': serializer.data})
        return Response(
            {'success': False, 'message': 'Validation error.', 'errors': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if bill.payment_status != 'pending':
        return Response(
            {'success': False, 'message': 'Only pending purchase bills can be deleted.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    bill.delete()
    return Response({'success': True, 'message': 'Purchase bill deleted successfully.'}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_products(request):
    vendor_name = request.GET.get('vendor_name', '').strip()
    if not vendor_name:
        return Response({'success': False, 'message': 'vendor_name is required.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = request.user.active_tenant
    product_ids = (
        PurchaseBillItem.objects.filter(
            purchase_bill__vendor_name=vendor_name,
            purchase_bill__created_by=tenant,
        )
        .values_list('product_id', flat=True)
        .distinct()
    )

    from inventory.models import Product

    products = Product.objects.filter(id__in=product_ids)
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def purchase_order_list_create(request):
    tenant = request.user.active_tenant

    if request.method == 'GET':
        orders = (
            PurchaseOrder.objects.filter(created_by=tenant)
            .order_by('-created_at')
            .prefetch_related('items__product')
        )
        serializer = PurchaseOrderSerializer(orders, many=True)
        return Response({'success': True, 'data': serializer.data})

    # Wrap POST handling to capture unexpected server errors for debugging.
    try:
        serializer = PurchaseOrderSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(created_by=tenant)
            return Response(
                {'success': True, 'message': 'Purchase order created successfully.', 'data': serializer.data},
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {'success': False, 'message': 'Validation error.', 'errors': serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        # Log full exception with traceback to server logs for diagnosis
        logging.exception('Unhandled error creating purchase order')
        # Return limited details to the client for debugging in development only
        return Response(
            {
                'success': False,
                'message': 'Internal server error while creating purchase order.',
                'details': str(exc),
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def purchase_order_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        po = PurchaseOrder.objects.prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except PurchaseOrder.DoesNotExist:
        return Response({'success': False, 'message': 'Purchase order not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = PurchaseOrderSerializer(po)
        return Response({'success': True, 'data': serializer.data})

    if request.method in ['PUT', 'PATCH']:
        if po.status not in ['draft', 'sent']:
            return Response({'success': False, 'message': 'Only draft or sent POs can be edited.'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = PurchaseOrderSerializer(po, data=request.data, partial=(request.method == 'PATCH'), context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'message': 'Purchase order updated successfully.', 'data': serializer.data})
        return Response({'success': False, 'message': 'Validation error.', 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    # DELETE
    if po.status not in ['draft', 'cancelled']:
        return Response({'success': False, 'message': 'Only draft or cancelled POs can be deleted.'}, status=status.HTTP_400_BAD_REQUEST)
    po.delete()
    return Response({'success': True, 'message': 'Purchase order deleted.'}, status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def purchase_order_convert_to_bill(request, pk):
    """Convert a PurchaseOrder into a PurchaseBill (marking as received)."""
    tenant = request.user.active_tenant
    try:
        po = PurchaseOrder.objects.prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except PurchaseOrder.DoesNotExist:
        return Response({'success': False, 'message': 'Purchase order not found.'}, status=status.HTTP_404_NOT_FOUND)

    if po.status == 'received':
        return Response({'success': False, 'message': 'Purchase order already received.'}, status=status.HTTP_400_BAD_REQUEST)

    # Create PurchaseBill from PO
    bill = PurchaseBill.objects.create(
        bill_number = f'PB-{str(po.id)[:8]}',
        bill_date = timezone.now().date(),
        vendor = po.vendor,
        vendor_name = po.vendor.name if po.vendor else '',
        total_amount = po.total_amount or 0,
        created_by = tenant,
    )

    items_created = 0
    for item in po.items.all():
        PurchaseBillItem.objects.create(
            purchase_bill = bill,
            product = item.product,
            batch = item.batch,
            quantity = item.quantity,
            unit = item.unit,
            price = item.price,
            discount = item.discount,
            tax = item.tax,
            amount = item.amount,
        )
        items_created += 1

    # Mark PO as received
    po.status = 'received'
    po.save(update_fields=['status'])

    serializer = PurchaseBillSerializer(bill)
    return Response({'success': True, 'message': 'Purchase order converted to bill.', 'data': serializer.data})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sales_invoice_list_create(request):
    tenant = request.user.active_tenant

    if request.method == 'GET':
        try:
            from django.db.models import Q
            invoices = (
                SalesInvoice.objects.filter(
                    Q(created_by=tenant) | Q(created_by__parent=tenant)
                )
                .select_related('customer')
                .prefetch_related('items__product')
                .order_by('-invoice_date', '-created_at')
            )
            customer_id = request.GET.get('customer')
            if customer_id:
                invoices = invoices.filter(customer_id=customer_id)

            status_filter = request.GET.get('status')
            if status_filter and status_filter != 'all':
                invoices = invoices.filter(status=status_filter)

            serializer = SalesInvoiceSerializer(invoices, many=True)
            return Response(serializer.data)
        except (ProgrammingError, DatabaseError) as exc:
            return Response(
                {
                    'error': 'Sales invoices unavailable due to database schema mismatch.',
                    'details': str(exc),
                    'action': 'Run migrations in backend container and retry.',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    invoice_number = request.data.get('invoice_number')
    if invoice_number and SalesInvoice.objects.filter(invoice_number=invoice_number, created_by=tenant).exists():
        return Response(
            {'error': 'Invoice number already exists', 'details': f'Invoice with number {invoice_number} already exists.'},
            status=status.HTTP_409_CONFLICT,
        )

    serializer = SalesInvoiceSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        serializer.save(created_by=tenant)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    return Response({'error': 'Validation failed', 'details': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_invoice_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        invoice = SalesInvoice.objects.select_related('customer', 'warehouse').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = SalesInvoiceSerializer(invoice)
    return Response(serializer.data)


@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_invoice_update_delete(request, pk):
    tenant = request.user.active_tenant
    try:
        invoice = SalesInvoice.objects.get(pk=pk, created_by=tenant)
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        if invoice.payment_status != 'pending':
            return Response({'error': 'Only pending sales invoices can be edited.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = SalesInvoiceSerializer(
            invoice,
            data=request.data,
            partial=(request.method == 'PATCH'),
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if invoice.payment_status != 'pending':
        return Response({'error': 'Only pending sales invoices can be deleted.'}, status=status.HTTP_400_BAD_REQUEST)

    invoice.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_next_invoice_number(request):
    prefix = request.GET.get('prefix', 'INV-')
    tenant_id = str(request.user.active_tenant.id)[:4].upper()
    full_prefix = f'{prefix}{tenant_id}-'

    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant,
        invoice_number__startswith=full_prefix,
    )

    max_num = 0
    for inv in invoices:
        suffix = inv.invoice_number.replace(full_prefix, '')
        try:
            num = int(suffix)
            if num > max_num:
                max_num = num
        except ValueError:
            continue

    next_num = max_num + 1
    return Response(
        {
            'success': True,
            'uuid_prefix': tenant_id,
            'next_number': f'{full_prefix}{next_num:03d}',
            'suffix': f'{next_num:03d}',
        }
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_summary_analytics(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    tenant = request.user.active_tenant
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Sales Analytics Request - start_date: {start_date}, end_date: {end_date}, tenant: {tenant.email}")

    # Filter for invoices created by the owner OR any of their team members
    from django.db.models import Q
    base_qs = SalesInvoice.objects.filter(
        Q(created_by=tenant) | Q(created_by__parent=tenant)
    ).exclude(status='draft')

    qs = base_qs
    if start_date:
        qs = qs.filter(invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice_date__lte=end_date)

    # Calculate using Python sum for absolute parity with the list and count
    all_qs_invoices = list(qs.only('total_amount'))
    total_revenue = sum((inv.total_amount for inv in all_qs_invoices), Decimal('0'))
    total_invoices = len(all_qs_invoices)
    
    logger.info(f"Sales Analytics Result - count: {total_invoices}, total: {total_revenue}")

    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    now = timezone.now().astimezone(ist)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month_qs = base_qs.filter(
        invoice_date__gte=this_month_start.date(),
    )

    this_month_invoices_list = list(this_month_qs.only('total_amount'))
    this_month_revenue = sum((inv.total_amount for inv in this_month_invoices_list), Decimal('0'))
    this_month_invoices = len(this_month_invoices_list)

    from billing.models_returns import CreditNote
    # Subtract returns for accuracy - also scoped to tenant+team
    returns_base_qs = CreditNote.objects.filter(
        Q(created_by=tenant) | Q(created_by__parent=tenant)
    )
    
    returns_qs = returns_base_qs
    if start_date:
        returns_qs = returns_qs.filter(date__gte=start_date)
    if end_date:
        returns_qs = returns_qs.filter(date__lte=end_date)
    total_returns = returns_qs.aggregate(total=Sum('total_amount'))['total'] or 0
    
    month_returns = returns_base_qs.filter(
        date__gte=this_month_start.date()
    ).aggregate(total=Sum('total_amount'))['total'] or 0

    return Response(
        {
            'success': True,
            'total_revenue': float(total_revenue - total_returns),
            'total_invoices': total_invoices,
            'this_month_revenue': float(this_month_revenue - month_returns),
            'this_month_invoices': this_month_invoices,
        }
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recalculate_invoice_totals(request):
    tenant = request.user.active_tenant
    invoices = SalesInvoice.objects.filter(
        Q(created_by=tenant) | Q(created_by__parent=tenant)
    )
    fixed_count = 0

    for invoice in invoices:
        items_total = sum(Decimal(str(item.amount or 0)) for item in invoice.items.all())
        if invoice.total_amount != items_total:
            invoice.total_amount = items_total
            invoice.save(update_fields=['total_amount'])
            fixed_count += 1

    return Response({'success': True, 'message': f'Fixed {fixed_count} invoices with incorrect totals'})

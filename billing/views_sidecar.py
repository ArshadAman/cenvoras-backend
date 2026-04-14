from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from .models_sidecar import SalesOrder, SalesOrderItem, DeliveryChallan, InvoiceSettings, Quotation, QuotationItem
from .serializers_sidecar import SalesOrderSerializer, DeliveryChallanSerializer, InvoiceSettingsSerializer, QuotationSerializer
from .models import SalesInvoice, SalesInvoiceItem
from cenvoras.pagination import StandardResultsSetPagination
import random
from datetime import date
from decimal import Decimal

# =============================================================================
# SALES ORDER VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sales_order_list_create(request):
    if request.method == 'GET':
        search = request.GET.get('search', '')
        orders = SalesOrder.objects.filter(created_by=request.user)
        
        if search:
            orders = orders.filter(
                order_number__icontains=search
            ) | orders.filter(
                customer__name__icontains=search
            )
            
        orders = orders.order_by('-date')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(orders, request)
        if page is not None:
            serializer = SalesOrderSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = SalesOrderSerializer(orders, many=True)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = SalesOrderSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_order_detail(request, pk):
    try:
        order = SalesOrder.objects.get(pk=pk, created_by=request.user)
    except SalesOrder.DoesNotExist:
        return Response({"success": False, "message": "Order not found"}, status=404)
        
    if request.method == 'GET':
        serializer = SalesOrderSerializer(order)
        return Response(serializer.data)
        
    elif request.method == 'PUT':
        serializer = SalesOrderSerializer(order, data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
    elif request.method == 'DELETE':
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_order_to_invoice(request, pk):
    try:
        order = SalesOrder.objects.get(pk=pk, created_by=request.user)
    except SalesOrder.DoesNotExist:
        return Response({"message": "Order not found"}, status=404)

    # Simple Conversion Logic
    invoice = SalesInvoice.objects.create(
        customer=order.customer,
        customer_name=order.customer.name,
        invoice_number=f"INV-{date.today().strftime('%Y%m%d')}-{random.randint(1000, 9999)}",
        invoice_date=date.today(),
        created_by=request.user,
        total_amount=order.total_amount
    )
    
    for item in order.items.all():
        SalesInvoiceItem.objects.create(
            sales_invoice=invoice,
            product=item.product,
            quantity=item.quantity,
            price=item.price,
            amount=item.amount,
            unit="pcs", 
            tax=item.product.tax
        )
        
    # Update Order Stage
    order.stage = 'completed'
    order.save()
    
    return Response({"message": "Converted successfully", "invoice_id": invoice.id})

# =============================================================================
# DELIVERY CHALLAN VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def delivery_challan_list_create(request):
    if request.method == 'GET':
        search = request.GET.get('search', '')
        challans = DeliveryChallan.objects.filter(created_by=request.user)
        
        if search:
            challans = challans.filter(
                challan_number__icontains=search
            ) | challans.filter(
                customer__name__icontains=search
            )
            
        challans = challans.order_by('-date')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(challans, request)
        if page is not None:
            serializer = DeliveryChallanSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = DeliveryChallanSerializer(challans, many=True)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = DeliveryChallanSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def delivery_challan_detail(request, pk):
    try:
        challan = DeliveryChallan.objects.get(pk=pk, created_by=request.user)
    except DeliveryChallan.DoesNotExist:
        return Response(status=404)
        
    if request.method == 'GET':
        serializer = DeliveryChallanSerializer(challan)
        return Response(serializer.data)
    elif request.method == 'PUT':
        serializer = DeliveryChallanSerializer(challan, data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        challan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

# =============================================================================
# INVOICE SETTINGS VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def invoice_settings_view(request):
    try:
        settings = InvoiceSettings.objects.get(user=request.user)
    except InvoiceSettings.DoesNotExist:
        settings = InvoiceSettings.objects.create(user=request.user)
        
    if request.method == 'GET':
        serializer = InvoiceSettingsSerializer(settings)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = InvoiceSettingsSerializer(settings, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# =============================================================================
# QUOTATION VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def quotation_list_create(request):
    tenant = request.user.active_tenant

    if request.method == 'GET':
        search = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '').strip()

        qs = Quotation.objects.filter(created_by=tenant).prefetch_related('items__product').order_by('-quotation_date', '-created_at')

        if search:
            qs = qs.filter(Q(quotation_number__icontains=search) | Q(customer_name__icontains=search))
        if status_filter and status_filter != 'all':
            qs = qs.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = QuotationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = QuotationSerializer(qs, many=True)
        return Response(serializer.data)

    serializer = QuotationSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def quotation_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        quotation = Quotation.objects.get(pk=pk, created_by=tenant)
    except Quotation.DoesNotExist:
        return Response({'message': 'Quotation not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response(QuotationSerializer(quotation).data)

    if request.method in ['PUT', 'PATCH']:
        serializer = QuotationSerializer(
            quotation,
            data=request.data,
            partial=(request.method == 'PATCH'),
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    quotation.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quotation_next_number(request):
    tenant = request.user.active_tenant
    prefix = request.GET.get('prefix', 'QT-')

    tenant_code = str(tenant.id)[:4].upper()
    full_prefix = f'{prefix}{tenant_code}-'
    quotations = Quotation.objects.filter(created_by=tenant, quotation_number__startswith=full_prefix)

    max_num = 0
    for q in quotations:
        suffix = q.quotation_number.replace(full_prefix, '')
        try:
            num = int(suffix)
            if num > max_num:
                max_num = num
        except ValueError:
            continue

    next_num = max_num + 1
    return Response({
        'success': True,
        'uuid_prefix': tenant_code,
        'next_number': f'{full_prefix}{next_num:03d}',
        'suffix': f'{next_num:03d}',
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quotation_convert_to_sales_order(request, pk):
    tenant = request.user.active_tenant
    try:
        quotation = Quotation.objects.prefetch_related('items').get(pk=pk, created_by=tenant)
    except Quotation.DoesNotExist:
        return Response({'message': 'Quotation not found'}, status=status.HTTP_404_NOT_FOUND)

    if quotation.status not in ['approved', 'partially_converted']:
        return Response(
            {'message': 'Only approved quotations can be converted to sales orders.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    approved_item_ids = request.data.get('approved_item_ids', [])
    selected_qs = quotation.items.filter(approval_status='approved', converted_to_order=False)
    if approved_item_ids:
        selected_qs = selected_qs.filter(id__in=approved_item_ids)

    selected_items = list(selected_qs)
    if not selected_items:
        return Response(
            {'message': 'No approved quotation items selected for conversion.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    order_total = sum(Decimal(str(item.amount)) for item in selected_items)
    next_index = SalesOrder.objects.filter(created_by=tenant).count() + 1
    order_number = f'SO-{tenant.id.hex[:4].upper()}-{next_index:03d}'

    order_customer = quotation.customer
    if not order_customer:
        from .models import Customer
        order_customer = Customer.objects.filter(name__iexact=quotation.customer_name, created_by=tenant).first()
        if not order_customer and quotation.customer_name:
            order_customer = Customer.objects.create(
                name=quotation.customer_name,
                address=quotation.customer_address,
                created_by=tenant,
            )

    if not order_customer:
        return Response({'message': 'Quotation must have a customer to convert.'}, status=status.HTTP_400_BAD_REQUEST)

    order = SalesOrder.objects.create(
        order_number=order_number,
        date=date.today(),
        customer=order_customer,
        total_amount=order_total,
        notes=f'Converted from quotation {quotation.quotation_number}',
        created_by=tenant,
    )

    for item in selected_items:
        SalesOrderItem.objects.create(
            order=order,
            product=item.product,
            quantity=item.quantity,
            price=item.price,
            amount=item.amount,
        )
        item.converted_to_order = True
        item.save(update_fields=['converted_to_order'])

    remaining = quotation.items.filter(approval_status='approved', converted_to_order=False).exists()
    quotation.status = 'partially_converted' if remaining else 'converted'
    quotation.save(update_fields=['status'])

    return Response({
        'message': 'Quotation converted to sales order successfully.',
        'sales_order_id': str(order.id),
        'sales_order_number': order.order_number,
    })

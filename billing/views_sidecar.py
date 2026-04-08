from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models_sidecar import SalesOrder, DeliveryChallan, InvoiceSettings
from .serializers_sidecar import SalesOrderSerializer, DeliveryChallanSerializer, InvoiceSettingsSerializer
from .models import SalesInvoice, SalesInvoiceItem
from cenvoras.pagination import StandardResultsSetPagination
import random
from datetime import date

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

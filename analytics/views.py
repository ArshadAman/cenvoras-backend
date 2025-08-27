from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from billing.models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem
from inventory.models import Product
from django.db.models import Sum, F
from datetime import datetime
import csv
from django.http import HttpResponse
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

# Create your views here.

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'csv' for CSV export", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Sales summary",
        examples={
            "application/json": {
                "total_sales": 150000,
                "sales_by_product": [
                    {"product__name": "Product A", "total": 90000},
                    {"product__name": "Product B", "total": 60000}
                ],
                "sales_by_customer": [
                    {"customer__name": "Customer X", "total": 100000},
                    {"customer__name": "Customer Y", "total": 50000}
                ],
                "sales_by_date": [
                    {"invoice_date": "2025-08-01", "total": 50000},
                    {"invoice_date": "2025-08-15", "total": 100000}
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_summary(request):
    """
    Returns total sales, sales by product, customer, and date.
    Optionally exports the data as CSV.
    """
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    export = request.query_params.get('export')

    qs = SalesInvoice.objects.filter(created_by=request.user)
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    total_sales = qs.aggregate(total=Sum('total_amount'))['total'] or 0

    items = SalesInvoiceItem.objects.filter(sales_invoice__in=qs)
    sales_by_product = items.values('product__name').annotate(total=Sum('amount')).order_by('-total')
    sales_by_customer = qs.values('customer__name').annotate(total=Sum('total_amount')).order_by('-total')
    sales_by_date = qs.values('invoice_date').annotate(total=Sum('total_amount')).order_by('invoice_date')

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="sales_summary.csv"'
        writer = csv.writer(response)
        writer.writerow(['Product', 'Total Sales'])
        for row in sales_by_product:
            writer.writerow([row['product__name'], row['total']])
        writer.writerow([])
        writer.writerow(['Customer', 'Total Sales'])
        for row in sales_by_customer:
            writer.writerow([row['customer__name'], row['total']])
        writer.writerow([])
        writer.writerow(['Date', 'Total Sales'])
        for row in sales_by_date:
            writer.writerow([row['invoice_date'], row['total']])
        writer.writerow([])
        writer.writerow(['Total Sales', total_sales])
        return response

    return Response({
        'total_sales': total_sales,
        'sales_by_product': sales_by_product,
        'sales_by_customer': sales_by_customer,
        'sales_by_date': sales_by_date,
    })

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'csv' for CSV export", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Purchase summary",
        examples={
            "application/json": {
                "total_purchases": 80000,
                "purchases_by_vendor": [
                    {"vendor_name": "Vendor A", "total": 50000},
                    {"vendor_name": "Vendor B", "total": 30000}
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def purchase_summary(request):
    """
    Returns total purchases and purchases by vendor.
    Optionally exports the data as CSV.
    """
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    export = request.query_params.get('export')

    qs = PurchaseBill.objects.filter(created_by=request.user)
    if date_from:
        qs = qs.filter(bill_date__gte=date_from)
    if date_to:
        qs = qs.filter(bill_date__lte=date_to)
    total_purchases = qs.aggregate(total=Sum('total_amount'))['total'] or 0
    purchases_by_vendor = qs.values('vendor_name').annotate(total=Sum('total_amount')).order_by('-total')

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="purchase_summary.csv"'
        writer = csv.writer(response)
        writer.writerow(['Vendor', 'Total Purchases'])
        for row in purchases_by_vendor:
            writer.writerow([row['vendor_name'], row['total']])
        writer.writerow([])
        writer.writerow(['Total Purchases', total_purchases])
        return response

    return Response({
        'total_purchases': total_purchases,
        'purchases_by_vendor': purchases_by_vendor,
    })

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'csv' for CSV export", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Inventory summary",
        examples={
            "application/json": {
                "products": [
                    {
                        "id": "uuid-1",
                        "name": "Product A",
                        "hsn_sac_code": "1234",
                        "stock": 50,
                        "unit": "pcs",
                        "low_stock_alert": 10
                    }
                ],
                "low_stock_alerts": [
                    {
                        "id": "uuid-2",
                        "name": "Product B",
                        "hsn_sac_code": "5678",
                        "stock": 5,
                        "unit": "pcs",
                        "low_stock_alert": 10
                    }
                ],
                "negative_stock": [],
                "total_inventory_value": 120000
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inventory_summary(request):
    """
    Returns current stock for all products and low stock alerts.
    Optionally exports the data as CSV.
    """
    export = request.query_params.get('export')
    products = Product.objects.filter(created_by=request.user)
    product_list = []
    low_stock = []
    for product in products:
        data = {
            'id': str(product.id),
            'name': product.name,
            'hsn_sac_code': product.hsn_sac_code,
            'stock': product.stock,
            'unit': product.unit,
            'low_stock_alert': product.low_stock_alert,
        }
        product_list.append(data)
        if product.low_stock_alert and product.stock <= product.low_stock_alert:
            low_stock.append(data)

    total_inventory_value = products.aggregate(
        value=Sum(F('stock') * F('price'))
    )['value'] or 0

    negative_stock = [data for data in product_list if data['stock'] < 0]

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="inventory_summary.csv"'
        writer = csv.writer(response)
        writer.writerow(['Product', 'HSN/SAC Code', 'Stock', 'Unit', 'Low Stock Alert'])
        for row in product_list:
            writer.writerow([row['name'], row['hsn_sac_code'], row['stock'], row['unit'], row['low_stock_alert']])
        writer.writerow([])
        writer.writerow(['Total Inventory Value', total_inventory_value])
        return response

    return Response({
        'products': product_list,
        'low_stock_alerts': low_stock,
        'negative_stock': negative_stock,
        'total_inventory_value': total_inventory_value,
    })

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'csv' for CSV export", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="GST summary",
        examples={
            "application/json": {
                "gst_collected": 18000,
                "gst_paid": 12000,
                "gst_payable": 6000,
                "gst_by_product": [
                    {"product__name": "Product A", "total_gst": 10000},
                    {"product__name": "Product B", "total_gst": 8000}
                ],
                "gst_by_month": [
                    {"month": 8, "total_gst": 18000}
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gst_summary(request):
    """
    Returns total GST collected (sales) and paid (purchases) in a date range.
    Optionally exports the data as CSV.
    """
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    export = request.query_params.get('export')

    # GST collected from sales
    sales_items = SalesInvoiceItem.objects.filter(sales_invoice__created_by=request.user)
    if date_from:
        sales_items = sales_items.filter(sales_invoice__invoice_date__gte=date_from)
    if date_to:
        sales_items = sales_items.filter(sales_invoice__invoice_date__lte=date_to)
    gst_collected = sales_items.aggregate(total_gst=Sum('tax'))['total_gst'] or 0

    # GST paid on purchases
    purchase_items = PurchaseBillItem.objects.filter(purchase_bill__created_by=request.user)
    if date_from:
        purchase_items = purchase_items.filter(purchase_bill__bill_date__gte=date_from)
    if date_to:
        purchase_items = purchase_items.filter(purchase_bill__bill_date__lte=date_to)
    gst_paid = purchase_items.aggregate(total_gst=Sum('tax'))['total_gst'] or 0

    # GST by product
    gst_by_product = sales_items.values('product__name').annotate(total_gst=Sum('tax')).order_by('-total_gst')
    gst_by_month = sales_items.annotate(month=F('sales_invoice__invoice_date__month')).values('month').annotate(total_gst=Sum('tax')).order_by('month')

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="gst_summary.csv"'
        writer = csv.writer(response)
        writer.writerow(['GST Type', 'Amount'])
        writer.writerow(['GST Collected', gst_collected])
        writer.writerow(['GST Paid', gst_paid])
        writer.writerow(['GST Payable', gst_collected - gst_paid])
        writer.writerow([])
        writer.writerow(['Product', 'GST Collected'])
        for row in gst_by_product:
            writer.writerow([row['product__name'], row['total_gst']])
        writer.writerow([])
        writer.writerow(['Month', 'GST Collected'])
        for row in gst_by_month:
            writer.writerow([row['month'], row['total_gst']])
        return response

    return Response({
        'gst_collected': gst_collected,
        'gst_paid': gst_paid,
        'gst_payable': gst_collected - gst_paid,
        'gst_by_product': gst_by_product,
        'gst_by_month': gst_by_month,
    })

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Dashboard summary",
        examples={
            "application/json": {
                "total_sales": 150000,
                "total_purchases": 80000,
                "total_inventory_value": 120000,
                "low_stock_count": 1,
                "gst_collected": 18000,
                "gst_paid": 12000,
                "gst_payable": 6000
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """
    Returns a summary of sales, purchases, inventory, and GST.
    """
    # Sales
    sales_qs = SalesInvoice.objects.filter(created_by=request.user)
    total_sales = sales_qs.aggregate(total=Sum('total_amount'))['total'] or 0

    # Purchases
    purchase_qs = PurchaseBill.objects.filter(created_by=request.user)
    total_purchases = purchase_qs.aggregate(total=Sum('total_amount'))['total'] or 0

    # Inventory
    products = Product.objects.filter(created_by=request.user)
    total_inventory_value = products.aggregate(
        value=Sum(F('stock') * F('price'))
    )['value'] or 0
    low_stock_count = products.filter(stock__lte=F('low_stock_alert')).count()

    # GST
    sales_items = SalesInvoiceItem.objects.filter(sales_invoice__created_by=request.user)
    gst_collected = sales_items.aggregate(total_gst=Sum('tax'))['total_gst'] or 0
    purchase_items = PurchaseBillItem.objects.filter(purchase_bill__created_by=request.user)
    gst_paid = purchase_items.aggregate(total_gst=Sum('tax'))['total_gst'] or 0
    gst_payable = gst_collected - gst_paid

    return Response({
        'total_sales': total_sales,
        'total_purchases': total_purchases,
        'total_inventory_value': total_inventory_value,
        'low_stock_count': low_stock_count,
        'gst_collected': gst_collected,
        'gst_paid': gst_paid,
        'gst_payable': gst_payable,
    })

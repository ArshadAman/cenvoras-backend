from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from billing.models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem
from inventory.models import Product, StockPoint
from django.db.models import Sum, F
from datetime import datetime
import csv
from django.http import HttpResponse
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.views.decorators.cache import cache_page

from cenvoras.cache_utils import CACHE_TTL_MEDIUM, cache_get_or_set, tenant_cache_key

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

    qs = SalesInvoice.objects.filter(created_by=request.user, status='final')
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    cache_key = tenant_cache_key('analytics', tenant.id, 'dashboard-summary')

    def build_summary():
        from django.db.models.functions import TruncMonth
        from collections import defaultdict
        from decimal import Decimal

        # Sales
        sales_qs = SalesInvoice.objects.filter(created_by=tenant, status='final')
        total_sales = sales_qs.aggregate(total=Sum('total_amount'))['total'] or 0

        # Purchases
        purchase_qs = PurchaseBill.objects.filter(created_by=tenant)
        total_purchases = purchase_qs.aggregate(total=Sum('total_amount'))['total'] or 0

        # Inventory
        products = Product.objects.filter(created_by=tenant)
        total_inventory_value = products.aggregate(
            value=Sum(F('stock') * F('price'))
        )['value'] or 0
        low_stock_count = products.filter(stock__lte=F('low_stock_alert')).count()

        # GST Calculation (FIXED: Calculate actual tax amount, not sum of percentages)
        # For sales: tax_amount = (quantity * price - discount_amount) * tax_rate / 100
        sales_items = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=tenant,
            sales_invoice__status='final'
        )
        gst_collected = Decimal('0.00')
        for item in sales_items:
            qty = Decimal(item.quantity)
            price = Decimal(item.price)
            discount_pct = Decimal(item.discount or 0)
            tax_rate = Decimal(item.tax or 0)

            line_value = qty * price
            discount_amount = (line_value * discount_pct) / Decimal('100')
            taxable_value = line_value - discount_amount
            tax_amount = (taxable_value * tax_rate) / Decimal('100')
            gst_collected += tax_amount

        purchase_items = PurchaseBillItem.objects.filter(purchase_bill__created_by=tenant)
        gst_paid = Decimal('0.00')
        for item in purchase_items:
            qty = Decimal(item.quantity)
            price = Decimal(item.price)
            discount_pct = Decimal(item.discount or 0)
            tax_rate = Decimal(item.tax or 0)

            line_value = qty * price
            discount_amount = (line_value * discount_pct) / Decimal('100')
            taxable_value = line_value - discount_amount
            tax_amount = (taxable_value * tax_rate) / Decimal('100')
            gst_paid += tax_amount

        gst_payable = gst_collected - gst_paid

        # Sales vs Purchases Chart Data (Monthly aggregation)
        sales_by_month = sales_qs.annotate(
            month=TruncMonth('invoice_date')
        ).values('month').annotate(
            total=Sum('total_amount')
        ).order_by('month')

        purchases_by_month = purchase_qs.annotate(
            month=TruncMonth('bill_date')
        ).values('month').annotate(
            total=Sum('total_amount')
        ).order_by('month')

        # Merge into chart format
        month_data = defaultdict(lambda: {'Sales': 0, 'Purchases': 0})
        for entry in sales_by_month:
            if entry['month']:
                month_name = entry['month'].strftime('%b %Y')
                month_data[month_name]['Sales'] = float(entry['total'] or 0)
        for entry in purchases_by_month:
            if entry['month']:
                month_name = entry['month'].strftime('%b %Y')
                month_data[month_name]['Purchases'] = float(entry['total'] or 0)

        # Convert to list for chart
        sales_vs_purchases = [
            {'name': month, 'Sales': data['Sales'], 'Purchases': data['Purchases']}
            for month, data in sorted(month_data.items())
        ]

        return {
            'total_sales': total_sales,
            'total_purchases': total_purchases,
            'total_inventory_value': total_inventory_value,
            'low_stock_count': low_stock_count,
            'gst_collected': float(gst_collected),
            'gst_paid': float(gst_paid),
            'gst_payable': float(gst_payable),
            'sales_vs_purchases': sales_vs_purchases,
        }

    return Response(cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_summary))
@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING, required=True),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING, required=True),
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'json' or 'csv'", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="GSTR-1 Report Data",
        examples={
            "application/json": [
                {
                    "gstin": "27ABCDE1234F1Z5",
                    "receiver_name": "Customer X",
                    "invoice_number": "INV-001",
                    "invoice_date": "2023-01-01",
                    "invoice_value": 1180.00,
                    "place_of_supply": "27-Maharashtra",
                    "invoice_type": "B2B",
                    "rate": 18.0,
                    "taxable_value": 1000.00,
                    "igst": 0,
                    "cgst": 90.00,
                    "sgst": 90.00,
                    "cess": 0
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr1_report(request):
    """
    Returns data for GSTR-1 filing (B2B, B2C Large, B2C Small).
    Aggregates data by Invoice and Tax Rate.
    """
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    export = request.query_params.get('export', 'json')

    if not date_from or not date_to:
        return Response({"error": "date_from and date_to are required"}, status=400)

    # Fetch User State (Place of Supply Origin)
    user_state_code = request.user.state
    if not user_state_code:
        # Default or Error? For now handle gracefully.
        user_state_code = "" 

    invoices = SalesInvoice.objects.filter(
        created_by=request.user,
        invoice_date__gte=date_from,
        invoice_date__lte=date_to
    ).prefetch_related('items', 'customer')

    report_data = []

    for inv in invoices:
        customer = inv.customer
        is_b2b = bool(customer and customer.gstin)
        # Determine Place of Supply
        pos = customer.state if customer and customer.state else user_state_code
        is_inter_state = pos != user_state_code

        # Invoice Type
        if is_b2b:
            inv_type = "B2B"
        elif inv.total_amount > 250000 and is_inter_state:
            inv_type = "B2CL" # B2C Large
        else:
            inv_type = "B2CS" # B2C Small

        # Group items by Tax Rate
        tax_groups = {} # { 18.0: { 'taxable': 0, 'tax_amt': 0 } }
        
        for item in inv.items.all():
            qty = item.quantity # Ignore free_quantity for tax val, usually
            price = float(item.price)
            discount_percent = float(item.discount)
            tax_rate = float(item.tax)
            
            # Line Taxable Value
            line_val = qty * price
            discount_amount = (line_val * discount_percent) / 100.0
            taxable_val = line_val - discount_amount
            
            # Tax Amount
            tax_amt = (taxable_val * tax_rate) / 100.0

            if tax_rate not in tax_groups:
                tax_groups[tax_rate] = {'taxable': 0.0, 'tax_amt': 0.0}
            
            tax_groups[tax_rate]['taxable'] += taxable_val
            tax_groups[tax_rate]['tax_amt'] += tax_amt

        # Create rows for report
        for rate, vals in tax_groups.items():
            igst = 0.0
            cgst = 0.0
            sgst = 0.0
            
            if is_inter_state:
                igst = vals['tax_amt']
            else:
                cgst = vals['tax_amt'] / 2.0
                sgst = vals['tax_amt'] / 2.0

            row = {
                "gstin": customer.gstin if (customer and customer.gstin) else "",
                "receiver_name": inv.customer_name or (customer.name if customer else "Unknown"),
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date,
                "invoice_value": float(inv.total_amount),
                "place_of_supply": pos,
                "invoice_type": inv_type,
                "rate": rate,
                "taxable_value": round(vals['taxable'], 2),
                "igst": round(igst, 2),
                "cgst": round(cgst, 2),
                "sgst": round(sgst, 2),
                "cess": 0 # Placeholder
            }
            report_data.append(row)

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="gstr1_report.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'GSTIN/UIN', 'Receiver Name', 'Invoice Number', 'Invoice Date', 
            'Invoice Value', 'Place Of Supply', 'Invoice Type', 
            'Rate (%)', 'Taxable Value', 'Integrated Tax', 'Central Tax', 'State/UT Tax', 'Cess'
        ])
        
        for r in report_data:
            writer.writerow([
                 r['gstin'], r['receiver_name'], r['invoice_number'], r['invoice_date'],
                 r['invoice_value'], r['place_of_supply'], r['invoice_type'],
                 r['rate'], r['taxable_value'], r['igst'], r['cgst'], r['sgst'], r['cess']
            ])
        return response

    return Response(report_data)


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('export', openapi.IN_QUERY, description="Set to 'csv' for CSV export", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Detailed Stock Summary (Batch-wise)",
        examples={
            "application/json": [
                {
                    "product_name": "Paracetamol 500mg",
                    "batch_number": "B001",
                    "expiry_date": "2025-12-31",
                    "quantity": 100,
                    "unit": "box",
                    "mrp": 50.0,
                    "value": 4000.0
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stock_summary_report(request):
    """
    Returns detailed batch-wise stock summary.
    This is the 'Closing Stock' report.
    """
    export = request.query_params.get('export', 'json')
    
    stock_points = StockPoint.objects.filter(
        product__created_by=request.user,
        quantity__gt=0
    ).select_related('product', 'batch', 'warehouse').order_by('product__name', 'batch__expiry_date')
    
    report_data = []
    
    for sp in stock_points:
        cost_price = float(sp.product.purchase_price) if sp.product.purchase_price else 0.0
        
        value = sp.quantity * cost_price
        
        data = {
            "product_name": sp.product.name,
            "batch_number": sp.batch.batch_number if sp.batch else "NA",
            "expiry_date": sp.batch.expiry_date if sp.batch else None,
            "warehouse": sp.warehouse.name if sp.warehouse else "Main",
            "quantity": sp.quantity,
            "unit": sp.product.unit,
            "cost_rate": cost_price,
            "stock_value": round(value, 2)
        }
        report_data.append(data)
        
    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="stock_summary.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Product Name', 'Batch No', 'Expiry Date', 'Warehouse', 
            'Quantity', 'Unit', 'Cost Rate', 'Stock Value'
        ])
        
        for r in report_data:
            writer.writerow([
                 r['product_name'], r['batch_number'], r['expiry_date'], r['warehouse'],
                 r['quantity'], r['unit'], r['cost_rate'], r['stock_value']
            ])
        return response

    return Response(report_data)


# ═══════════════════════════════════════════════════════════════
# SMART DASHBOARD - Intelligent Business Assistant
# ═══════════════════════════════════════════════════════════════

@swagger_auto_schema(
    method='get',
    operation_description="Get intelligent dashboard with business insights, warnings, and actionable metrics",
    responses={200: openapi.Response(
        description="Smart dashboard data",
        examples={
            "application/json": {
                "pulse": {"sales_today": 15000, "net_profit_today": 3500},
                "warnings": [{"type": "low_stock", "severity": "yellow", "title": "Low Stock: Product X"}],
                "insights": {"top_5_products": []},
                "gst_shield": {"turnover_percent": 45, "days_until_due": 12},
                "health_status": {"status": "green", "emoji": "🟢"}
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def smart_dashboard(request):
    """
    Smart Dashboard API - Returns intelligent business metrics
    
    Includes:
    - Pulse: Today's key metrics (sales, profit, udhaar)
    - Warnings: Actionable alerts (low stock, payment due, dead stock)
    - Insights: Business intelligence (bestsellers, slow movers, margins)
    - GST Shield: Compliance tracking (turnover, due dates, payable)
    - Health Status: Overall business health indicator (🟢🟡🔴)
    """
    from .smart_dashboard import SmartDashboard
    
    tenant = getattr(request.user, 'active_tenant', request.user)
    cache_key = tenant_cache_key('analytics', tenant.id, 'smart-dashboard')

    def build_dashboard():
        dashboard = SmartDashboard(request.user)
        return dashboard.get_full_dashboard()

    return Response(cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_dashboard))


# ═══════════════════════════════════════════════════════════════
# ML PREDICTIONS - Machine Learning Powered Insights
# ═══════════════════════════════════════════════════════════════

@swagger_auto_schema(
    method='get',
    operation_description="Get ML-powered predictions: Sales Forecast and Restock Predictions",
    responses={200: openapi.Response(
        description="ML Predictions",
        examples={
            "application/json": {
                "sales_forecast": {
                    "forecast": [{"date": "2026-02-05", "predicted_sales": 15000}],
                    "predicted_total": 105000,
                    "trend": "growing"
                },
                "restock_predictions": {
                    "predictions": [{"product_name": "Laptop Pro", "days_to_reorder": 3}]
                }
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
@cache_page(60 * 60 * 24)  # Cache ML predictions for 24 hours
def ml_predictions(request):
    """
    ML Predictions API - Sales Forecasting and Restock Predictions
    
    Sales Forecast:
    - 7-day sales prediction using Linear Regression
    - Trend analysis (growing/stable/declining)
    - Confidence level based on data variance
    
    Restock Predictions:
    - Days until stockout for each product
    - Suggested reorder dates
    - Urgency levels (critical/high/medium/low)
    """
    from .ml_predictions import MLPredictions
    
    ml = MLPredictions(request.user)
    return Response(ml.get_all_predictions())

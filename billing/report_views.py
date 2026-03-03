from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import SalesInvoice, SalesInvoiceItem, Customer
from .serializers import SalesInvoiceSerializer
from django.utils import timezone
from django.db.models import Sum, F, DecimalField, Value
from django.db.models.functions import Coalesce
import datetime

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def overdue_bills_report(request):
    """
    Get a list of overdue bills (Due Date < Today AND Balance > 0).
    For now, since we don't track per-invoice balance (only customer global balance),
    we can return invoices that are past due date for customers who have a positive balance.
    
    Ideally, we should track 'amount_paid' on each invoice to know if *that specific* invoice is unpaid.
    Gap: 'SalesInvoice' doesn't have 'paid_amount' or 'status' field yet.
    
    Compromise for Phase 2: 
    Return all invoices where due_date < today.
    """
    today = timezone.now().date()
    # Filter invoices created by user, due date passed
    overdue_invoices = SalesInvoice.objects.filter(
        created_by=request.user, 
        due_date__lt=today
    ).order_by('due_date')
    
    # We should filter out those that are fully paid, but we don't track per-invoice payment yet.
    # We only assume if Created > Balance, some might be paid.
    # For a true report, we need to allocate payments to invoices (Knock-off).
    # That is complex. For now, LIST ALL invoices past due date.
    
    serializer = SalesInvoiceSerializer(overdue_invoices, many=True)
    
    # Enrich data with 'days_overdue'
    data = serializer.data
    for item in data:
        due_date = datetime.datetime.strptime(item['due_date'], "%Y-%m-%d").date()
        item['days_overdue'] = (today - due_date).days
        
    return Response(data)


# =============================================================================
# Feature 15: Item-Wise Goods Account (P&L per Product)
# =============================================================================
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def item_wise_pl_report(request):
    """
    Item-wise Profit & Loss.
    For each product, calculate total revenue, cost, and profit from invoice items.
    Optional: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    queryset = SalesInvoiceItem.objects.filter(
        sales_invoice__created_by=request.user
    )

    # Date filters
    date_from = request.query_params.get('from')
    date_to = request.query_params.get('to')
    if date_from:
        queryset = queryset.filter(sales_invoice__invoice_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(sales_invoice__invoice_date__lte=date_to)

    # Group by product
    from collections import defaultdict
    product_data = defaultdict(lambda: {'revenue': 0, 'cost': 0, 'qty_sold': 0, 'product_name': '', 'unit': ''})

    items = queryset.select_related('product', 'batch')
    for item in items:
        pid = str(item.product.id)
        product_data[pid]['product_name'] = item.product.name
        product_data[pid]['unit'] = item.unit or item.product.unit
        product_data[pid]['qty_sold'] += item.quantity
        product_data[pid]['revenue'] += float(item.amount)

        # Cost: use batch cost_price if available, else product.price (purchase price)
        cost_per_unit = float(item.batch.cost_price) if item.batch and item.batch.cost_price else float(item.product.price)
        product_data[pid]['cost'] += cost_per_unit * item.quantity

    results = []
    for pid, d in product_data.items():
        profit = d['revenue'] - d['cost']
        margin = (profit / d['revenue'] * 100) if d['revenue'] else 0
        results.append({
            'product_id': pid,
            'product_name': d['product_name'],
            'unit': d['unit'],
            'qty_sold': d['qty_sold'],
            'revenue': round(d['revenue'], 2),
            'cost': round(d['cost'], 2),
            'profit': round(profit, 2),
            'margin_pct': round(margin, 1),
        })

    # Sort by profit descending
    results.sort(key=lambda x: x['profit'], reverse=True)

    total_revenue = sum(r['revenue'] for r in results)
    total_cost = sum(r['cost'] for r in results)
    total_profit = sum(r['profit'] for r in results)

    return Response({
        'count': len(results),
        'summary': {
            'total_revenue': round(total_revenue, 2),
            'total_cost': round(total_cost, 2),
            'total_profit': round(total_profit, 2),
            'overall_margin_pct': round((total_profit / total_revenue * 100) if total_revenue else 0, 1),
        },
        'results': results,
    })

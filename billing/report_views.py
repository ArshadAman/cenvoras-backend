from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import SalesInvoice, SalesInvoiceItem, Customer, BillPaymentStatus
from django.utils import timezone
from django.db.models import Sum, F, DecimalField, Value, ExpressionWrapper
from django.db.models.functions import Coalesce
from decimal import Decimal

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def overdue_bills_report(request):
    """Get overdue invoices with true invoice-level outstanding amounts."""
    today = timezone.now().date()
    tenant = getattr(request.user, 'active_tenant', request.user)

    outstanding_expr = ExpressionWrapper(
        Coalesce(F('total_amount'), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))) -
        Coalesce(F('amount_paid'), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))),
        output_field=DecimalField(max_digits=12, decimal_places=2)
    )

    overdue_invoices = (
        SalesInvoice.objects.filter(
            created_by=tenant,
            due_date__lt=today,
            payment_status__in=[BillPaymentStatus.PENDING, BillPaymentStatus.PARTIAL_PAID],
        )
        .annotate(outstanding_amount=outstanding_expr)
        .filter(outstanding_amount__gt=0)
        .select_related('customer')
        .order_by('due_date')
    )

    data = []
    for invoice in overdue_invoices:
        due_date = invoice.due_date
        data.append({
            'id': str(invoice.id),
            'invoice_number': invoice.invoice_number,
            'invoice_date': invoice.invoice_date,
            'due_date': due_date,
            'days_overdue': (today - due_date).days if due_date else 0,
            'customer_id': str(invoice.customer_id) if invoice.customer_id else None,
            'customer_name': invoice.customer.name if invoice.customer else (invoice.customer_name or 'Unknown Customer'),
            'total_amount': float(invoice.total_amount or 0),
            'amount_paid': float(invoice.amount_paid or 0),
            'outstanding_amount': float(invoice.outstanding_amount or 0),
            'payment_status': invoice.payment_status,
        })

    return Response({
        'count': len(data),
        'total_overdue_amount': float(sum((Decimal(str(item['outstanding_amount'])) for item in data), Decimal('0'))),
        'results': data,
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def customer_balance_reconciliation(request):
    """
    Compare customer current balance vs sum of invoice outstanding.
    Positive `unapplied_credit` means payment/credit exists without invoice linkage.
    Positive `unmapped_debit` means customer balance exceeds open invoices.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)

    customers = Customer.objects.filter(created_by=tenant).order_by('name')
    invoices = SalesInvoice.objects.filter(created_by=tenant)

    if request.query_params.get('customer'):
        customer_id = request.query_params.get('customer')
        customers = customers.filter(id=customer_id)
        invoices = invoices.filter(customer_id=customer_id)

    invoice_rows = invoices.values('customer_id').annotate(
        outstanding=Sum(
            ExpressionWrapper(
                Coalesce(F('total_amount'), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))) -
                Coalesce(F('amount_paid'), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
    )

    outstanding_by_customer = {
        row['customer_id']: (row['outstanding'] or Decimal('0'))
        for row in invoice_rows
        if row['customer_id']
    }

    rows = []
    total_unapplied_credit = Decimal('0')
    total_unmapped_debit = Decimal('0')

    for customer in customers:
        current_balance = Decimal(str(customer.current_balance or 0))
        invoice_outstanding = Decimal(str(outstanding_by_customer.get(customer.id, Decimal('0')) or 0))
        difference = invoice_outstanding - current_balance

        unapplied_credit = difference if difference > 0 else Decimal('0')
        unmapped_debit = (-difference) if difference < 0 else Decimal('0')

        total_unapplied_credit += unapplied_credit
        total_unmapped_debit += unmapped_debit

        rows.append({
            'customer_id': str(customer.id),
            'customer_name': customer.name,
            'current_balance': float(current_balance),
            'invoice_outstanding': float(invoice_outstanding),
            'difference': float(difference),
            'unapplied_credit': float(unapplied_credit),
            'unmapped_debit': float(unmapped_debit),
        })

    rows.sort(key=lambda item: abs(item['difference']), reverse=True)

    return Response({
        'count': len(rows),
        'summary': {
            'total_unapplied_credit': float(total_unapplied_credit),
            'total_unmapped_debit': float(total_unmapped_debit),
            'net_reconciliation_gap': float(total_unmapped_debit - total_unapplied_credit),
        },
        'results': rows,
    })


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

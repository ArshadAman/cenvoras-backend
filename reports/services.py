from decimal import Decimal
from django.db.models import Sum, F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from inventory.models import Product, StockPoint, ProductBatch
from billing.models import SalesInvoiceItem, PurchaseBillItem
from ledger.models import GeneralLedgerEntry

from cenvoras.cache_utils import (
    CACHE_TTL_LONG,
    CACHE_TTL_MEDIUM,
    cache_get_or_set,
    global_cache_key,
    tenant_cache_key,
)

def get_stock_valuation(tenant=None):
    """
    Calculate current stock valuation based on Weighted Average.
    """
    cache_key = tenant_cache_key('reports', getattr(tenant, 'id', None), 'stock-valuation') if tenant else global_cache_key('reports', 'stock-valuation')

    def build_value():
        valuation = []
        total_value = Decimal('0.00')

        # Product.stock is a cached field, we can use it directly
        products = Product.objects.select_related('meta')
        if tenant:
            products = products.filter(created_by=tenant)

        for product in products:
            stock = Decimal(str(product.stock or 0))
            cost_price = Decimal(str(product.price or 0))
            sale_price = Decimal(str(product.sale_price or 0))

            # Use sale_price as fallback if purchase cost is missing or zero.
            avg_cost = cost_price if cost_price > 0 else sale_price
            value = stock * avg_cost
            total_value += value

            valuation.append({
                'id': product.id,
                'name': product.name,
                # 'sku': product.sku, # Product has no SKU field yet
                'stock': stock,
                'avg_cost': avg_cost,
                'total_value': value,
                # 'category': product.category.name # Product has no category FK yet, simplistic model
            })

        return {
            'total_value': total_value,
            'items': valuation,
        }

    return cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_value)

def get_expiry_report(days_threshold=30, tenant=None):
    """
    Get batches expiring within `days_threshold` that have stock.
    """
    cache_key = tenant_cache_key('reports', getattr(tenant, 'id', None), 'expiry-report', f'days-{days_threshold}') if tenant else global_cache_key('reports', 'expiry-report', f'days-{days_threshold}')

    def build_report():
        today = timezone.now().date()
        limit_date = today + timezone.timedelta(days=days_threshold)

        # Filter batches that have stock in any stock point
        batches = ProductBatch.objects.filter(
            expiry_date__lte=limit_date,
            stock_points__quantity__gt=0,  # Check related StockPoints
        ).distinct().select_related('product').annotate(
            total_stock=Coalesce(Sum('stock_points__quantity'), 0)
        )
        if tenant:
            batches = batches.filter(product__created_by=tenant)

        report = []
        for batch in batches:
            total_stock = batch.total_stock or 0
            if total_stock > 0:
                days_left = (batch.expiry_date - today).days
                status = 'Expired' if days_left < 0 else 'Expiring Soon'

                report.append({
                    'product_name': batch.product.name,
                    'batch_number': batch.batch_number,
                    'expiry_date': batch.expiry_date,
                    'days_left': days_left,
                    'stock': total_stock,
                    'status': status,
                })

        return sorted(report, key=lambda x: x['days_left'])

    return cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_report)

def get_item_wise_profit(start_date, end_date, tenant=None):
    """
    Calculate Gross Profit per Item: Sales - Cost of Goods Sold (COGS).
    COGS = Avg Purchase Price * Qty Sold.
    """
    cache_key = tenant_cache_key('reports', getattr(tenant, 'id', None), 'item-wise-profit', str(start_date), str(end_date)) if tenant else global_cache_key('reports', 'item-wise-profit', str(start_date), str(end_date))

    def build_report():
        # 1. Get all sales in date range
        sales = SalesInvoiceItem.objects.filter(
            sales_invoice__invoice_date__range=[start_date, end_date]
        ).select_related('product', 'batch')
        if tenant:
            sales = sales.filter(sales_invoice__created_by=tenant)

        item_stats = {}

        for sale in sales:
            pid = sale.product.id
            if pid not in item_stats:
                item_stats[pid] = {
                    'name': sale.product.name,
                    'qty_sold': 0,
                    'revenue': Decimal('0.00'),
                    'cogs': Decimal('0.00'),
                }

            stats = item_stats[pid]
            qty = Decimal(str(sale.quantity))
            stats['qty_sold'] += sale.quantity
            
            # Revenue = (quantity * price - discount), before tax
            sale_price = Decimal(str(sale.price))
            discount = Decimal(str(sale.discount or 0))
            base_amount = qty * sale_price
            discount_amount = (base_amount * discount) / Decimal('100')
            line_revenue = base_amount - discount_amount
            stats['revenue'] += line_revenue

            # COGS: batch.cost_price -> product.price -> product.sale_price (fallback)
            if sale.batch and sale.batch.cost_price:
                cost_price = Decimal(str(sale.batch.cost_price))
            elif sale.product.price:
                cost_price = Decimal(str(sale.product.price))
            else:
                # Fallback to sale_price if no cost data
                cost_price = Decimal(str(sale.product.sale_price or 0))
            
            stats['cogs'] += (cost_price * qty)

        report = []
        total_revenue = Decimal('0.00')
        total_profit = Decimal('0.00')

        for pid, stats in item_stats.items():
            gross_profit = stats['revenue'] - stats['cogs']
            margin_percent = (gross_profit / stats['revenue'] * 100) if stats['revenue'] > 0 else 0

            total_revenue += stats['revenue']
            total_profit += gross_profit

            report.append({
                'name': stats['name'],
                'qty_sold': stats['qty_sold'],
                'revenue': stats['revenue'],
                'cogs': stats['cogs'],
                'gross_profit': gross_profit,
                'margin_percent': round(margin_percent, 2)
            })

        return {
            'total_revenue': total_revenue,
            'total_profit': total_profit,
            'items': sorted(report, key=lambda x: x['gross_profit'], reverse=True)
        }

    return cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_report)

def get_stock_ledger(product_id, start_date=None, end_date=None, tenant=None):
    """
    Generate a chronological item cardex / stock ledger for a specific product.
    Matches all In/Out movements across bills, invoices, returns, and journals.
    """
    from billing.models import PurchaseBillItem, SalesInvoiceItem
    from billing.models_returns import CreditNoteItem, DebitNoteItem
    from inventory.models_sidecar import StockJournalItem
    
    cache_key = tenant_cache_key('reports', getattr(tenant, 'id', None), 'stock-ledger', product_id, str(start_date), str(end_date)) if tenant else global_cache_key('reports', 'stock-ledger', product_id, str(start_date), str(end_date))

    def build_ledger():
        transactions = []

        product_filter = {'product_id': product_id}
        if tenant:
            product_filter['product__created_by'] = tenant

        # Purchases (In)
        purchases = PurchaseBillItem.objects.filter(**product_filter).select_related('purchase_bill', 'batch')
        if tenant:
            purchases = purchases.filter(purchase_bill__created_by=tenant)
        for p in purchases:
            transactions.append({
                'date': p.purchase_bill.bill_date,
                'type': 'Purchase',
                'reference': p.purchase_bill.bill_number,
                'qty_in': p.quantity,
                'qty_out': 0,
                'batch': p.batch.batch_number if p.batch else None
            })

        # Sales (Out)
        sales = SalesInvoiceItem.objects.filter(**product_filter).select_related('sales_invoice', 'batch')
        if tenant:
            sales = sales.filter(sales_invoice__created_by=tenant)
        for s in sales:
            transactions.append({
                'date': s.sales_invoice.invoice_date,
                'type': 'Sales',
                'reference': s.sales_invoice.invoice_number,
                'qty_in': 0,
                'qty_out': s.quantity,
                'batch': s.batch.batch_number if s.batch else None
            })

        # Credit Notes / Sales Return (In)
        cnotes = CreditNoteItem.objects.filter(**product_filter).select_related('credit_note', 'batch')
        if tenant:
            cnotes = cnotes.filter(credit_note__created_by=tenant)
        for c in cnotes:
            transactions.append({
                'date': c.credit_note.date,
                'type': 'Sales Return',
                'reference': c.credit_note.credit_note_number,
                'qty_in': c.quantity,
                'qty_out': 0,
                'batch': c.batch.batch_number if c.batch else None
            })

        # Debit Notes / Purchase Return (Out)
        dnotes = DebitNoteItem.objects.filter(**product_filter).select_related('debit_note', 'batch')
        if tenant:
            dnotes = dnotes.filter(debit_note__created_by=tenant)
        for d in dnotes:
            transactions.append({
                'date': d.debit_note.date,
                'type': 'Purchase Return',
                'reference': d.debit_note.debit_note_number,
                'qty_in': 0,
                'qty_out': d.quantity,
                'batch': d.batch.batch_number if d.batch else None
            })

        # Stock Journal (In/Out depending on qty sign)
        journals = StockJournalItem.objects.filter(**product_filter).select_related('journal', 'batch')
        if tenant:
            journals = journals.filter(journal__created_by=tenant)
        for j in journals:
            transactions.append({
                'date': j.journal.date,
                'type': f'Stock Journal ({j.journal.adjustment_type})',
                'reference': j.journal.voucher_no,
                'qty_in': j.quantity if j.quantity > 0 else 0,
                'qty_out': abs(j.quantity) if j.quantity < 0 else 0,
                'batch': j.batch.batch_number if j.batch else None
            })

        # Sort chronologically
        transactions.sort(key=lambda x: x['date'])

        # Calculate running balance
        running_balance = 0
        for i, t in enumerate(transactions):
            running_balance += t['qty_in']
            running_balance -= t['qty_out']
            t['balance'] = running_balance
            t['id'] = i  # simple unique id for frontend mapped to index

        # Filter by date range AFTER running balance is calculated
        if start_date:
            transactions = [t for t in transactions if t['date'] >= start_date]
        if end_date:
            transactions = [t for t in transactions if t['date'] <= end_date]

        return transactions

    return cache_get_or_set(cache_key, CACHE_TTL_MEDIUM, build_ledger)

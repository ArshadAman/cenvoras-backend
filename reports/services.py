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
    Calculate current stock valuation based on real-time Weighted Average Cost (WAC).

    Accounting Valuation Hierarchy:
    1. Active Batches on Hand: Volume-weighted average of currently stocked batches.
       WAC = Sum(stock_point.quantity * batch.cost_price) / Sum(stock_point.quantity)
    2. Purchase Bill History: Volume-weighted average of all supplier purchase bills.
       WAC = Sum(item.quantity * item.price) / Sum(item.quantity + item.free_quantity)
    3. Baseline Catalog Cost: product.price (opening/catalog cost)
    4. Fallback: product.sale_price or 0.00

    Optimized with bulk SQL aggregation to operate in O(1) queries (zero N+1 loops).
    """
    cache_key = tenant_cache_key('reports', getattr(tenant, 'id', None), 'stock-valuation') if tenant else global_cache_key('reports', 'stock-valuation')

    def build_value():
        valuation = []
        total_value = Decimal('0.00')

        # Active products for tenant
        products = Product.objects.select_related('meta').filter(is_active=True)
        if tenant:
            products = products.filter(created_by=tenant)

        # 1. Bulk aggregate on-hand batch costs (Tier 1 WAC)
        batch_sp_qs = StockPoint.objects.filter(
            quantity__gt=0,
            batch__cost_price__gt=0
        )
        if tenant:
            batch_sp_qs = batch_sp_qs.filter(warehouse__created_by=tenant)

        batch_valuations = (
            batch_sp_qs.values('batch__product_id')
            .annotate(
                total_qty=Sum('quantity'),
                total_val=Sum(F('quantity') * F('batch__cost_price'))
            )
        )
        batch_wac_map = {}
        for row in batch_valuations:
            qty = row['total_qty']
            val = row['total_val']
            if qty and qty > 0 and val is not None:
                batch_wac_map[row['batch__product_id']] = Decimal(str(val)) / Decimal(str(qty))

        # 2. Bulk aggregate supplier purchase bill history (Tier 2 WAC)
        purchase_items_qs = PurchaseBillItem.objects.filter(
            quantity__gt=0,
            price__gt=0
        )
        if tenant:
            purchase_items_qs = purchase_items_qs.filter(purchase_bill__created_by=tenant)

        purchase_valuations = (
            purchase_items_qs.values('product_id')
            .annotate(
                total_qty=Sum(F('quantity') + F('free_quantity')),
                total_val=Sum(F('quantity') * F('price'))
            )
        )
        purchase_wac_map = {}
        for row in purchase_valuations:
            qty = row['total_qty']
            val = row['total_val']
            if qty and qty > 0 and val is not None:
                purchase_wac_map[row['product_id']] = Decimal(str(val)) / Decimal(str(qty))

        # 3. Compute valuation for each product in-memory
        for product in products:
            stock = Decimal(str(product.stock or 0))
            cost_price = Decimal(str(product.price or 0))
            sale_price = Decimal(str(product.sale_price or 0))

            if product.id in batch_wac_map:
                avg_cost = batch_wac_map[product.id]
                method = 'batch_moving_average'
            elif product.id in purchase_wac_map:
                avg_cost = purchase_wac_map[product.id]
                method = 'purchase_history_average'
            elif cost_price > 0:
                avg_cost = cost_price
                method = 'catalog_cost'
            else:
                avg_cost = sale_price
                method = 'sale_price_fallback'

            avg_cost = avg_cost.quantize(Decimal('0.01'))
            if stock > 0:
                value = (stock * avg_cost).quantize(Decimal('0.01'))
            else:
                value = Decimal('0.00')

            total_value += value

            valuation.append({
                'id': product.id,
                'name': product.name,
                'stock': stock,
                'unit': product.unit,
                'avg_cost': avg_cost,
                'total_value': value,
                'valuation_method': method,
            })

        return {
            'total_value': total_value.quantize(Decimal('0.01')),
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

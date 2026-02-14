from decimal import Decimal
from django.db.models import Sum, F, Q
from django.utils import timezone
from inventory.models import Product, StockPoint, ProductBatch
from billing.models import SalesInvoiceItem, PurchaseBillItem
from ledger.models import GeneralLedgerEntry

def get_stock_valuation():
    """
    Calculate current stock valuation based on Weighted Average.
    """
    valuation = []
    total_value = Decimal('0.00')
    
    # Product.stock is a cached field, we can use it directly
    products = Product.objects.all().select_related('meta')
    
    for product in products:
        stock = product.stock
        # Use sale_price as fallback for now if price (cost) is 0
        avg_cost = product.price if product.price > 0 else product.sale_price 
        
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
        'items': valuation
    }

def get_expiry_report(days_threshold=30):
    """
    Get batches expiring within `days_threshold` that have stock.
    """
    today = timezone.now().date()
    limit_date = today + timezone.timedelta(days=days_threshold)
    
    # Filter batches that have stock in any stock point
    batches = ProductBatch.objects.filter(
        expiry_date__lte=limit_date, 
        stock_points__quantity__gt=0 # Check related StockPoints
    ).distinct().select_related('product')
    
    report = []
    for batch in batches:
        # Calculate total stock for this batch across all warehouses
        total_stock = batch.stock_points.aggregate(total=Sum('quantity'))['total'] or 0
        
        if total_stock > 0:
            days_left = (batch.expiry_date - today).days
            status = 'Expired' if days_left < 0 else 'Expiring Soon'
            
            report.append({
                'product_name': batch.product.name,
                'batch_number': batch.batch_number,
                'expiry_date': batch.expiry_date,
                'days_left': days_left,
                'stock': total_stock,
                'status': status
            })
        
    return sorted(report, key=lambda x: x['days_left'])

def get_item_wise_profit(start_date, end_date):
    """
    Calculate Gross Profit per Item: Sales - Cost of Goods Sold (COGS).
    COGS = Avg Purchase Price * Qty Sold.
    """
    # 1. Get all sales in date range
    sales = SalesInvoiceItem.objects.filter(
        sales_invoice__invoice_date__range=[start_date, end_date]
    ).select_related('product')
    
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
        stats['qty_sold'] += sale.quantity
        stats['revenue'] += sale.amount
        
        # COGS Estimate: Use product.price (Purchase Price) * Qty
        purchase_price = sale.product.price 
        stats['cogs'] += (purchase_price * sale.quantity)
        
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

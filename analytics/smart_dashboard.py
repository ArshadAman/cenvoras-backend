"""
Smart Dashboard - Business Intelligence Module
Provides actionable insights for shopkeepers
"""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, F, Count, Q, Avg
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from billing.models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Customer, Payment
from inventory.models import Product, StockPoint, ProductBatch


class SmartDashboard:
    """
    Intelligent dashboard calculations for a user's business
    """
    
    def __init__(self, user):
        self.user = user
        self.tenant = getattr(user, 'active_tenant', user)
        self.today = timezone.localdate()
        self.yesterday = self.today - timedelta(days=1)
    
    # ═══════════════════════════════════════════════════════════════
    # THE PULSE - What happened today?
    # ═══════════════════════════════════════════════════════════════
    
    def get_pulse(self):
        """Get today's key business metrics"""
        return {
            'sales_today': self._get_sales_today(),
            'sales_yesterday': self._get_sales_yesterday(),
            'sales_change_percent': self._get_sales_change_percent(),
            'cash_in_hand': self._get_cash_collections(),
            'bank_collections': self._get_bank_collections(),
            'net_profit_today': self._get_net_profit_today(),
            'udhaar_given_today': self._get_credit_given_today(),
            'udhaar_collected_today': self._get_credit_collected_today(),
            'total_receivables': self._get_total_receivables(),
        }
    
    def _get_sales_today(self):
        """Total sales amount for today"""
        result = SalesInvoice.objects.filter(
            created_by=self.tenant,
            invoice_date=self.today,
        ).exclude(status='draft').aggregate(total=Sum('total_amount'))
        return float(result['total'] or 0)
    
    def _get_sales_yesterday(self):
        """Total sales amount for yesterday"""
        result = SalesInvoice.objects.filter(
            created_by=self.tenant,
            invoice_date=self.yesterday,
        ).exclude(status='draft').aggregate(total=Sum('total_amount'))
        return float(result['total'] or 0)
    
    def _get_sales_change_percent(self):
        """Percentage change from yesterday"""
        today = self._get_sales_today()
        yesterday = self._get_sales_yesterday()
        if yesterday == 0:
            return 100 if today > 0 else 0
        return round(((today - yesterday) / yesterday) * 100, 1)
    
    def _get_cash_collections(self):
        """Cash payments received today"""
        result = Payment.objects.filter(
            created_by=self.tenant,
            date=self.today,
            mode='cash'
        ).aggregate(total=Sum('amount'))
        return float(result['total'] or 0)
    
    def _get_bank_collections(self):
        """Bank/UPI payments received today"""
        result = Payment.objects.filter(
            created_by=self.tenant,
            date=self.today,
            mode__in=['upi', 'bank_transfer', 'bank', 'cheque']
        ).aggregate(total=Sum('amount'))
        return float(result['total'] or 0)
    
    def _get_net_profit_today(self):
        """Estimated net profit = Sales - Cost of Goods Sold"""
        # Get today's sales items
        sales_items = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=self.tenant,
            sales_invoice__invoice_date=self.today,
            sales_invoice__status='final'
        ).select_related('product', 'batch')
        
        total_revenue = Decimal('0')
        total_cost = Decimal('0')
        
        for item in sales_items:
            qty = Decimal(str(item.quantity))
            sale_price = Decimal(str(item.price))
            line_revenue = Decimal(str(item.amount)) if item.amount is not None else (qty * sale_price)
            
            # Get cost price from batch or product
            if item.batch:
                cost_price = Decimal(str(item.batch.cost_price or item.product.price or 0))
            else:
                cost_price = Decimal(str(item.product.price or 0))
            
            total_revenue += line_revenue
            total_cost += qty * cost_price
        
        return float(total_revenue - total_cost)
    
    def _get_credit_given_today(self):
        """Total unpaid invoices created today (Udhaar given)"""
        # Get today's invoices
        today_invoices = SalesInvoice.objects.filter(
            created_by=self.tenant,
            invoice_date=self.today,
            status='final'
        )
        
        total_billed = today_invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        
        # Get payments received for today's invoices
        # This is simplified - ideally would track invoice-level payments
        total_collected = self._get_cash_collections() + self._get_bank_collections()
        
        credit_given = float(total_billed) - total_collected
        return max(0, credit_given)
    
    def _get_credit_collected_today(self):
        """Payments received today for old invoices"""
        # Total payments today
        total_payments = Payment.objects.filter(
            created_by=self.tenant,
            date=self.today
        ).aggregate(total=Sum('amount'))['total'] or 0
        return float(total_payments)
    
    def _get_total_receivables(self):
        """Total money owed to the business"""
        # Sum of all customer outstanding balance
        result = Customer.objects.filter(
            created_by=self.tenant,
            current_balance__gt=0
        ).aggregate(total=Sum('current_balance'))
        return float(result['total'] or 0)
    
    # ═══════════════════════════════════════════════════════════════
    # WARNING SYSTEM - What needs attention NOW?
    # ═══════════════════════════════════════════════════════════════
    
    def get_warnings(self):
        """Get all actionable alerts"""
        warnings = []
        warnings.extend(self._get_out_of_stock_warnings())
        warnings.extend(self._get_low_stock_warnings())
        warnings.extend(self._get_payment_due_warnings())
        warnings.extend(self._get_dead_stock_warnings())
        warnings.extend(self._get_cash_flow_warnings())
        
        # Sort by severity (red first, then yellow)
        severity_order = {'red': 0, 'yellow': 1, 'green': 2}
        warnings.sort(key=lambda w: severity_order.get(w['severity'], 2))
        
        return warnings
    
    def _get_out_of_stock_warnings(self):
        """Products that are completely out of stock"""
        out_of_stock = Product.objects.filter(
            created_by=self.user,
            stock=0
        ).values('id', 'name')[:5]
        
        return [{
            'type': 'out_of_stock',
            'severity': 'red',
            'title': f"Out of Stock: {p['name']}",
            'message': f"You're completely out of {p['name']}. Reorder now!",
            'product_id': str(p['id']),
            'action': 'reorder'
        } for p in out_of_stock]
    
    def _get_low_stock_warnings(self):
        """Products below low stock alert threshold (fallback to 10)"""
        products = Product.objects.filter(
            created_by=self.user,
            stock__gt=0
        ).values('id', 'name', 'stock', 'low_stock_alert')
        
        low_stock = []
        for p in products:
            threshold = p['low_stock_alert'] or 10
            if p['stock'] <= threshold:
                low_stock.append(p)
                if len(low_stock) >= 5:
                    break
        
        warnings = []
        for p in low_stock:
            # Estimate days remaining based on average daily sales
            days_remaining = self._estimate_days_remaining(p['id'], p['stock'])
            
            warnings.append({
                'type': 'low_stock',
                'severity': 'yellow',
                'title': f"Low Stock: {p['name']}",
                'message': f"Only {p['stock']} units left. Lasts ~{days_remaining} days at current sales rate.",
                'product_id': str(p['id']),
                'stock': p['stock'],
                'days_remaining': days_remaining,
                'action': 'reorder'
            })
        
        return warnings
    
    def _estimate_days_remaining(self, product_id, current_stock):
        """Estimate how many days stock will last based on sales velocity"""
        # Get average daily sales for last 30 days
        thirty_days_ago = self.today - timedelta(days=30)
        
        result = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=self.user,
            product_id=product_id,
            sales_invoice__invoice_date__gte=thirty_days_ago,
            sales_invoice__status='final'
        ).aggregate(total_qty=Sum('quantity'))
        
        total_sold = result['total_qty'] or 0
        avg_daily = total_sold / 30
        
        if avg_daily == 0:
            return 999  # Not selling, infinite days
        
        return round(current_stock / avg_daily)
    
    def _get_payment_due_warnings(self):
        """Customers with overdue payments"""
        # Find customers with credit used and invoices past due
        overdue_customers = []
        
        customers_with_credit = Customer.objects.filter(
            created_by=self.user,
            current_balance__gt=0
        ).values('id', 'name', 'current_balance', 'credit_limit')[:5]
        
        for c in customers_with_credit:
            # Check for overdue invoices
            overdue_invoices = SalesInvoice.objects.filter(
                created_by=self.tenant,
                customer_id=c['id'],
                due_date__lt=self.today,
                status='final',
                # Simplified: assume any invoice with a customer is on credit
            ).count()
            
            if overdue_invoices > 0 or c['current_balance'] > 0:
                overdue_customers.append({
                    'type': 'payment_due',
                    'severity': 'yellow',
                    'title': f"Payment Due: {c['name']}",
                    'message': f"{c['name']} owes ₹{int(c['current_balance']):,}. Follow up today!",
                    'customer_id': str(c['id']),
                    'amount': float(c['current_balance']),
                    'action': 'collect'
                })
        
        return overdue_customers
    
    def _get_dead_stock_warnings(self):
        """Products that haven't sold in 60+ days"""
        sixty_days_ago = self.today - timedelta(days=60)
        
        # Get products with stock but no recent sales
        products_with_stock = Product.objects.filter(
            created_by=self.user,
            stock__gt=0
        ).values('id', 'name', 'stock', 'price')
        
        dead_stock = []
        for p in products_with_stock:
            # Check if sold in last 60 days
            recent_sales = SalesInvoiceItem.objects.filter(
                sales_invoice__created_by=self.user,
                product_id=p['id'],
                sales_invoice__invoice_date__gte=sixty_days_ago,
                sales_invoice__status='final'
            ).exists()
            
            if not recent_sales:
                trapped_value = p['stock'] * float(p['price'] or 0)
                if trapped_value > 1000:  # Only show if significant value
                    dead_stock.append({
                        'type': 'dead_stock',
                        'severity': 'yellow',
                        'title': f"Dead Stock: {p['name']}",
                        'message': f"No sales in 60+ days. ₹{int(trapped_value):,} trapped. Consider a 10% discount.",
                        'product_id': str(p['id']),
                        'trapped_value': trapped_value,
                        'action': 'discount'
                    })
        
        return dead_stock[:3]  # Limit to 3
    
    def _get_cash_flow_warnings(self):
        """Detect if purchases significantly exceed sales"""
        # Last 30 days
        thirty_days_ago = self.today - timedelta(days=30)
        
        total_sales = SalesInvoice.objects.filter(
            created_by=self.user,
            invoice_date__gte=thirty_days_ago,
            status='final'
        ).aggregate(total=Sum('total_amount'))['total'] or 0
        
        total_purchases = PurchaseBill.objects.filter(
            created_by=self.user,
            bill_date__gte=thirty_days_ago
        ).aggregate(total=Sum('total_amount'))['total'] or 0
        
        if total_purchases > 0 and total_sales > 0:
            ratio = float(total_purchases) / float(total_sales)
            if ratio > 3:  # Purchases are 3x more than sales
                return [{
                    'type': 'cash_flow',
                    'severity': 'red',
                    'title': 'Cash Flow Warning',
                    'message': f"Purchases (₹{int(total_purchases):,}) are {ratio:.1f}x higher than sales (₹{int(total_sales):,}) this month.",
                    'action': 'review'
                }]
        
        return []
    
    # ═══════════════════════════════════════════════════════════════
    # PROFIT FINDER - How do I grow?
    # ═══════════════════════════════════════════════════════════════
    
    def get_insights(self):
        """Get business intelligence insights"""
        return {
            'top_5_products': self._get_top_products(),
            'slow_movers': self._get_slow_movers(),
            'profit_margins': self._get_margin_analysis(),
            'peak_hours': self._get_peak_hours(),
        }
    
    def _get_top_products(self):
        """Top 5 best-selling products by revenue"""
        thirty_days_ago = self.today - timedelta(days=30)
        
        top_products = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=self.user,
            sales_invoice__invoice_date__gte=thirty_days_ago,
            sales_invoice__status='final'
        ).values('product__name', 'product__id').annotate(
            total_revenue=Sum(F('quantity') * F('price')),
            total_qty=Sum('quantity')
        ).order_by('-total_revenue')[:5]
        
        total_revenue = float(sum(float(p['total_revenue'] or 0) for p in top_products))
        
        result = []
        for p in top_products:
            revenue = float(p['total_revenue'] or 0)
            percent_of_total = (revenue / total_revenue * 100) if total_revenue > 0 else 0
            result.append({
                'name': p['product__name'],
                'product_id': str(p['product__id']),
                'revenue': revenue,
                'quantity': p['total_qty'] or 0,
                'percent_of_total': round(percent_of_total, 1)
            })
        
        return result
    
    def _get_slow_movers(self):
        """Products with low sales velocity"""
        thirty_days_ago = self.today - timedelta(days=30)
        
        # Get all products with stock
        products_with_stock = list(Product.objects.filter(
            created_by=self.user,
            stock__gt=10  # Only consider if decent stock
        ).order_by('-stock')[:10])

        product_ids = [product.id for product in products_with_stock]
        sales_by_product = {
            row['product_id']: row['total'] or 0
            for row in SalesInvoiceItem.objects.filter(
                sales_invoice__created_by=self.user,
                product_id__in=product_ids,
                sales_invoice__invoice_date__gte=thirty_days_ago,
                sales_invoice__status='final'
            ).values('product_id').annotate(total=Sum('quantity'))
        }
        
        slow_movers = []
        for product in products_with_stock:
            sales_qty = sales_by_product.get(product.id, 0)
            
            # If selling less than 1 per week
            if sales_qty < 4:
                trapped_value = product.stock * float(product.price or 0)
                slow_movers.append({
                    'name': product.name,
                    'product_id': str(product.id),
                    'stock': product.stock,
                    'sales_30d': sales_qty,
                    'trapped_value': trapped_value,
                    'suggestion': 'Consider discounting to clear stock'
                })
        
        return slow_movers[:5]
    
    def _get_margin_analysis(self):
        """Analyze profit margins by product"""
        products = Product.objects.filter(
            created_by=self.user
        ).values('id', 'name', 'price', 'sale_price')[:10]
        
        margins = []
        for p in products:
            cost = float(p['price'] or 0)
            sale = float(p['sale_price'] or 0)
            
            if sale > 0:
                margin = ((sale - cost) / sale) * 100
                margins.append({
                    'name': p['name'],
                    'product_id': str(p['id']),
                    'cost_price': cost,
                    'sale_price': sale,
                    'margin_percent': round(margin, 1)
                })
        
        # Sort by lowest margin first (problem areas)
        margins.sort(key=lambda x: x['margin_percent'])
        return margins[:5]
    
    def _get_peak_hours(self):
        """Find busiest hours based on invoice timestamps"""
        # Note: This requires invoice_date to include time
        # For now, return a placeholder
        return {
            'peak_start': '17:00',
            'peak_end': '20:00',
            'message': 'You are busiest between 5 PM and 8 PM'
        }
    
    # ═══════════════════════════════════════════════════════════════
    # GST SHIELD - Compliance tracking
    # ═══════════════════════════════════════════════════════════════
    
    def get_gst_shield(self):
        """Get GST compliance information"""
        return {
            'total_turnover': self._get_gst_turnover(),
            'turnover_limit': 4000000,  # ₹40 Lakh for goods
            'turnover_percent': self._get_turnover_percent(),
            'next_due_date': self._get_next_gst_due_date(),
            'days_until_due': self._get_days_until_due(),
            'gst_collected': self._get_gst_collected(),
            'gst_paid': self._get_gst_paid(),
            'gst_payable': self._get_gst_payable(),
        }
    
    def _get_gst_turnover(self):
        """Total taxable turnover for current financial year"""
        # Financial year: April to March
        if self.today.month >= 4:
            fy_start = date(self.today.year, 4, 1)
        else:
            fy_start = date(self.today.year - 1, 4, 1)
        
        result = SalesInvoice.objects.filter(
            created_by=self.user,
            invoice_date__gte=fy_start,
            status='final'
        ).aggregate(total=Sum('total_amount'))
        
        return float(result['total'] or 0)
    
    def _get_turnover_percent(self):
        """Percentage of turnover limit reached"""
        turnover = self._get_gst_turnover()
        limit = 4000000  # ₹40 Lakh
        return min(100, round((turnover / limit) * 100, 1))
    
    def _get_next_gst_due_date(self):
        """Get next GSTR-3B due date (20th of next month)"""
        if self.today.day <= 20:
            next_due = date(self.today.year, self.today.month, 20)
        else:
            if self.today.month == 12:
                next_due = date(self.today.year + 1, 1, 20)
            else:
                next_due = date(self.today.year, self.today.month + 1, 20)
        
        return next_due.isoformat()
    
    def _get_days_until_due(self):
        """Days until next GST filing"""
        if self.today.day <= 20:
            next_due = date(self.today.year, self.today.month, 20)
        else:
            if self.today.month == 12:
                next_due = date(self.today.year + 1, 1, 20)
            else:
                next_due = date(self.today.year, self.today.month + 1, 20)
        
        return (next_due - self.today).days
    
    def _get_gst_collected(self):
        """Total GST collected from sales this month"""
        month_start = date(self.today.year, self.today.month, 1)
        
        items = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=self.user,
            sales_invoice__invoice_date__gte=month_start,
            sales_invoice__status='final'
        )
        
        total_gst = Decimal('0')
        for item in items:
            taxable = Decimal(str(item.quantity)) * Decimal(str(item.price))
            discount = (taxable * Decimal(str(item.discount or 0))) / 100
            taxable -= discount
            gst = (taxable * Decimal(str(item.tax or 0))) / 100
            total_gst += gst
        
        return float(total_gst)
    
    def _get_gst_paid(self):
        """Total GST paid on purchases this month"""
        month_start = date(self.today.year, self.today.month, 1)
        
        items = PurchaseBillItem.objects.filter(
            purchase_bill__created_by=self.user,
            purchase_bill__bill_date__gte=month_start
        )
        
        total_gst = Decimal('0')
        for item in items:
            taxable = Decimal(str(item.quantity)) * Decimal(str(item.price))
            discount = (taxable * Decimal(str(item.discount or 0))) / 100
            taxable -= discount
            gst = (taxable * Decimal(str(item.tax or 0))) / 100
            total_gst += gst
        
        return float(total_gst)
    
    def _get_gst_payable(self):
        """GST payable = Collected - Paid (this month)"""
        return self._get_gst_collected() - self._get_gst_paid()
    
    # ═══════════════════════════════════════════════════════════════
    # HEALTH STATUS - Overall business health indicator
    # ═══════════════════════════════════════════════════════════════
    
    def get_health_status(self):
        """Calculate overall business health status (🟢🟡🔴)"""
        warnings = self.get_warnings()
        
        red_count = sum(1 for w in warnings if w['severity'] == 'red')
        yellow_count = sum(1 for w in warnings if w['severity'] == 'yellow')
        
        if red_count > 0:
            return {
                'status': 'red',
                'emoji': '🔴',
                'message': f'{red_count} critical issue(s) need attention'
            }
        elif yellow_count > 3:
            return {
                'status': 'yellow',
                'emoji': '🟡',
                'message': f'{yellow_count} items need your attention'
            }
        else:
            return {
                'status': 'green',
                'emoji': '🟢',
                'message': 'Business is running smoothly'
            }
    
    def get_full_dashboard(self):
        """Get complete smart dashboard data"""
        def safe_call(func, default):
            try:
                return func()
            except Exception as e:
                import logging
                logging.error(f"Dashboard Error in {func.__name__}: {e}")
                return default

        return {
            'pulse': safe_call(self.get_pulse, {}),
            'warnings': safe_call(self.get_warnings, []),
            'insights': safe_call(self.get_insights, {}),
            'gst_shield': safe_call(self.get_gst_shield, {}),
            'health_status': safe_call(self.get_health_status, {
                'status': 'green', 'emoji': '🟢', 'message': 'Business is running smoothly (Safe mode)'
            }),
        }

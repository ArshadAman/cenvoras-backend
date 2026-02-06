"""
ML Predictions Module
Sales Forecasting and Restock Predictions using simple ML
No external dependencies - pure Python implementation
"""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Sum, Avg, F
from django.db.models.functions import TruncDate

from billing.models import SalesInvoice, SalesInvoiceItem
from inventory.models import Product


# ═══════════════════════════════════════════════════════════════
# PURE PYTHON MATH HELPERS (No numpy needed)
# ═══════════════════════════════════════════════════════════════

def mean(values):
    """Calculate mean of a list"""
    if not values:
        return 0
    return sum(values) / len(values)

def variance(values):
    """Calculate variance of a list"""
    if len(values) < 2:
        return 0
    m = mean(values)
    return sum((x - m) ** 2 for x in values) / len(values)


class MLPredictions:
    """
    Machine Learning predictions for business intelligence
    - Sales Forecasting (7-day prediction)
    - Restock Predictions (when to reorder)
    """
    
    def __init__(self, user):
        self.user = user
        self.today = date.today()
    
    # ═══════════════════════════════════════════════════════════════
    # SALES FORECASTING
    # ═══════════════════════════════════════════════════════════════
    
    def get_sales_forecast(self, days_ahead=7):
        """
        Predict sales for the next N days using Linear Regression
        """
        # Get historical daily sales (last 30 days)
        thirty_days_ago = self.today - timedelta(days=30)
        
        daily_sales = SalesInvoice.objects.filter(
            created_by=self.user,
            invoice_date__gte=thirty_days_ago
        ).annotate(
            day=TruncDate('invoice_date')
        ).values('day').annotate(
            total=Sum('total_amount')
        ).order_by('day')
        
        if len(daily_sales) < 7:
            # Not enough data for meaningful prediction
            return {
                'forecast': [],
                'predicted_total': 0,
                'confidence': 'low',
                'message': 'Need at least 7 days of sales data for accurate predictions'
            }
        
        # Prepare data for regression
        sales_values = [float(d['total'] or 0) for d in daily_sales]
        
        # Simple Linear Regression (pure Python)
        n = len(sales_values)
        x_values = list(range(n))
        
        x_mean = mean(x_values)
        y_mean = mean(sales_values)
        
        # Calculate slope
        numerator = sum((x_values[i] - x_mean) * (sales_values[i] - y_mean) for i in range(n))
        denominator = sum((x_values[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            slope = 0
        else:
            slope = numerator / denominator
        
        intercept = y_mean - slope * x_mean
        
        # Predict future days
        forecast = []
        for i in range(days_ahead):
            future_x = n + i
            predicted_value = max(0, slope * future_x + intercept)  # Can't be negative
            forecast_date = self.today + timedelta(days=i + 1)
            
            forecast.append({
                'date': forecast_date.isoformat(),
                'day_name': forecast_date.strftime('%A'),
                'predicted_sales': round(predicted_value, 2)
            })
        
        predicted_total = sum(f['predicted_sales'] for f in forecast)
        
        # Calculate trend
        trend = 'stable'
        if slope > 100:
            trend = 'growing'
        elif slope < -100:
            trend = 'declining'
        
        # Confidence based on data variance
        data_variance = variance(sales_values)
        confidence = 'high' if data_variance < 100000 else 'medium' if data_variance < 500000 else 'low'
        
        return {
            'forecast': forecast,
            'predicted_total': round(predicted_total, 2),
            'daily_average': round(predicted_total / days_ahead, 2),
            'trend': trend,
            'confidence': confidence,
            'historical_avg': round(y_mean, 2),
            'message': self._generate_forecast_message(predicted_total, trend, days_ahead)
        }
    
    def _generate_forecast_message(self, predicted_total, trend, days):
        """Generate human-readable forecast message"""
        if trend == 'growing':
            return f"📈 Sales are trending up! Predicted ₹{int(predicted_total):,} over the next {days} days."
        elif trend == 'declining':
            return f"📉 Sales are trending down. Expected ₹{int(predicted_total):,} over the next {days} days. Consider promotions."
        else:
            return f"📊 Sales are stable. Predicted ₹{int(predicted_total):,} over the next {days} days."
    
    # ═══════════════════════════════════════════════════════════════
    # RESTOCK PREDICTIONS
    # ═══════════════════════════════════════════════════════════════
    
    def get_restock_predictions(self, safety_buffer_days=3):
        """
        Predict when products will run out and when to reorder
        Uses sales velocity + safety stock calculation
        """
        products = Product.objects.filter(
            created_by=self.user,
            stock__gt=0  # Only products with stock
        )
        
        predictions = []
        thirty_days_ago = self.today - timedelta(days=30)
        
        for product in products:
            # Calculate average daily sales velocity
            sales_data = SalesInvoiceItem.objects.filter(
                sales_invoice__created_by=self.user,
                product=product,
                sales_invoice__invoice_date__gte=thirty_days_ago
            ).aggregate(
                total_qty=Sum('quantity'),
                total_revenue=Sum(F('quantity') * F('price'))
            )
            
            total_sold = float(sales_data['total_qty'] or 0)
            avg_daily_sales = total_sold / 30
            
            if avg_daily_sales <= 0:
                continue  # Skip products with no sales
            
            # Calculate days until stockout
            current_stock = float(product.stock)
            days_until_stockout = current_stock / avg_daily_sales
            stockout_date = self.today + timedelta(days=int(days_until_stockout))
            
            # Calculate reorder date (stockout - lead time - safety buffer)
            lead_time_days = 3  # Assume 3 days for supplier delivery
            reorder_date = stockout_date - timedelta(days=lead_time_days + safety_buffer_days)
            
            # Calculate suggested reorder quantity (2 weeks of sales + safety stock)
            suggested_qty = int(avg_daily_sales * 14 + (avg_daily_sales * safety_buffer_days))
            
            # Urgency level
            days_to_reorder = (reorder_date - self.today).days
            if days_to_reorder <= 0:
                urgency = 'critical'
                urgency_color = 'red'
            elif days_to_reorder <= 3:
                urgency = 'high'
                urgency_color = 'orange'
            elif days_to_reorder <= 7:
                urgency = 'medium'
                urgency_color = 'yellow'
            else:
                urgency = 'low'
                urgency_color = 'green'
            
            predictions.append({
                'product_id': str(product.id),
                'product_name': product.name,
                'current_stock': product.stock,
                'avg_daily_sales': round(avg_daily_sales, 1),
                'days_until_stockout': round(days_until_stockout, 1),
                'stockout_date': stockout_date.isoformat(),
                'reorder_date': reorder_date.isoformat(),
                'days_to_reorder': days_to_reorder,
                'suggested_qty': suggested_qty,
                'urgency': urgency,
                'urgency_color': urgency_color,
                'message': self._generate_restock_message(product.name, days_to_reorder, reorder_date)
            })
        
        # Sort by urgency (critical first)
        urgency_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        predictions.sort(key=lambda x: (urgency_order.get(x['urgency'], 4), x['days_to_reorder']))
        
        return {
            'predictions': predictions[:10],  # Top 10 most urgent
            'critical_count': sum(1 for p in predictions if p['urgency'] == 'critical'),
            'high_count': sum(1 for p in predictions if p['urgency'] == 'high'),
            'total_products_analyzed': len(predictions)
        }
    
    def _generate_restock_message(self, product_name, days_to_reorder, reorder_date):
        """Generate human-readable restock message"""
        if days_to_reorder <= 0:
            return f"🚨 URGENT: Order {product_name} NOW! You should have reordered already."
        elif days_to_reorder <= 3:
            return f"⚠️ Order {product_name} by {reorder_date.strftime('%b %d')} to avoid stockout."
        elif days_to_reorder <= 7:
            return f"📦 Plan to restock {product_name} by {reorder_date.strftime('%b %d')}."
        else:
            return f"✅ {product_name} is well-stocked. Reorder around {reorder_date.strftime('%b %d')}."
    
    # ═══════════════════════════════════════════════════════════════
    # COMBINED PREDICTIONS
    # ═══════════════════════════════════════════════════════════════
    
    def get_all_predictions(self):
        """Get all ML predictions in one call"""
        return {
            'sales_forecast': self.get_sales_forecast(),
            'restock_predictions': self.get_restock_predictions(),
        }

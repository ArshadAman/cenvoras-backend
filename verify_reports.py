import os
import django
from decimal import Decimal
import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from reports.services import get_stock_valuation, get_expiry_report, get_item_wise_profit
from django.utils import timezone

def run_test():
    print("🚀 Starting Report Verification...")
    
    # 1. Stock Valuation
    print("\n[Test 1] Stock Valuation")
    val_data = get_stock_valuation()
    print(f"Total Value: {val_data['total_value']}")
    print(f"Items: {len(val_data['items'])}")
    if len(val_data['items']) > 0:
        print("✅ SUCCESS: Valuation data generated.")
    else:
        print("⚠️ WARNING: No items to value.")

    # 2. Expiry Report
    print("\n[Test 2] Expiry Report")
    exp_data = get_expiry_report(days_threshold=365) # Check next year
    print(f"Expiring Batches Found: {len(exp_data)}")
    if len(exp_data) > 0:
        print(f"Example: {exp_data[0]['product_name']} expires on {exp_data[0]['expiry_date']}")
    print("✅ SUCCESS: Expiry logic ran.")

    # 3. Profit & Loss
    print("\n[Test 3] Item-Wise P&L")
    today = timezone.now().date()
    start_date = today - datetime.timedelta(days=365)
    pl_data = get_item_wise_profit(start_date, today)
    print(f"Total Revenue: {pl_data['total_revenue']}")
    print(f"Total Profit: {pl_data['total_profit']}")
    
    if pl_data['total_revenue'] > 0:
         print("✅ SUCCESS: P&L calculated based on sales.")
    else:
         print("⚠️ WARNING: No sales found for P&L.")

if __name__ == "__main__":
    run_test()

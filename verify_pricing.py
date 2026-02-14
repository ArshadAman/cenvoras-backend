import os
import django
from decimal import Decimal
import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.utils import timezone
from django.contrib.auth import get_user_model
from inventory.models import Product
from inventory.models_pricing import PriceList, PriceListItem, Scheme
from billing.models import Customer
from billing.models_sidecar import PartyMeta
from services.pricing import calculate_price

User = get_user_model()
user = User.objects.first()

def run_test():
    print("🚀 Starting Pricing Verification...")
    
    # 1. Setup Data
    product, _ = Product.objects.get_or_create(
        name="Pricing Test Product", 
        created_by=user, 
        defaults={'price': 100, 'sale_price': 100}
    )
    
    # 2. Setup Customer (Wholesaler)
    customer, _ = Customer.objects.get_or_create(name="Wholesale Buyer", created_by=user, defaults={'email': 'wholesale@test.com'})
    PartyMeta.objects.update_or_create(
        customer=customer,
        defaults={'party_category': 'wholesaler'}
    )
    
    # 3. Test Standard Price
    print("\n[Test 1] Standard Price")
    res = calculate_price(product, customer, quantity=1)
    print(f"Standard Result: {res}")
    if res['final_price'] == 100:
        print("✅ SUCCESS: Standard Price correct.")
    else:
        print("❌ FAILED: Standard Price incorrect.")

    # 4. Create Price List
    print("\n[Test 2] Price List Override")
    pl, _ = PriceList.objects.get_or_create(name="Wholesale List", created_by=user, defaults={'party_category': 'wholesaler'})
    PriceListItem.objects.update_or_create(
        price_list=pl,
        product=product,
        defaults={'price': 80, 'min_qty': 1}
    )
    
    res = calculate_price(product, customer, quantity=1)
    print(f"Price List Result: {res}")
    if res['final_price'] == 80:
        print("✅ SUCCESS: Price List applied.")
    else:
        print("❌ FAILED: Price List not applied.")

    # 5. Create Scheme (BOGO)
    print("\n[Test 3] Scheme (BOGO)")
    today = timezone.now().date()
    scheme, _ = Scheme.objects.get_or_create(
        name="BOGO Offer",
        product=product,
        created_by=user,
        defaults={
            'scheme_type': 'bogo',
            'start_date': today,
            'end_date': today + datetime.timedelta(days=30),
            'min_qty': 2,
            'free_product': product, # Buy X Get Same X
            'free_qty': 1
        }
    )
    
    res = calculate_price(product, customer, quantity=2)
    print(f"Scheme Result: {res}")
    if res['scheme'] and res['scheme']['type'] == 'bogo' and res['scheme']['free_qty'] == 1:
        print("✅ SUCCESS: BOGO Scheme applied.")
    else:
        print("❌ FAILED: BOGO Scheme not applied.")

if __name__ == "__main__":
    run_test()

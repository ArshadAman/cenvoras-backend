import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.contrib.auth import get_user_model
from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from billing.models_sidecar import PartyMeta
from billing.serializers import SalesInvoiceSerializer
from inventory.models import Product, Warehouse

User = get_user_model()
user = User.objects.first()

def run_test():
    print("🚀 Starting CRM Verification...")
    
    # 1. Setup Data
    warehouse, _ = Warehouse.objects.get_or_create(name="CRM Test Warehouse", created_by=user)
    product, _ = Product.objects.get_or_create(
        name="CRM Product", 
        created_by=user, 
        defaults={'price': 100, 'sale_price': 100}
    )
    
    # 2. Create Customer with Credit Limit
    print("\n[Test 1] Credit Limit Enforcement")
    customer, created = Customer.objects.get_or_create(
        name="Credit Check User", 
        created_by=user,
        defaults={'email': 'credit@test.com'}
    )
    if not customer.email:
        customer.email = 'credit@test.com'
    customer.credit_limit = 1000
    customer.current_balance = 0
    customer.allow_credit = False # STRICT check
    customer.save()
    print(f"Customer: {customer.name}, Limit: {customer.credit_limit}, Balance: {customer.current_balance}, Allow Credit: {customer.allow_credit}")
    
    # 3. Try to create Invoice > Limit
    # Invoice Amount = 1500 (15 * 100)
    data = {
        'customer_name': customer.name,
        'customer_email': customer.email, # Needed to find customer in serializer
        'invoice_number': 'INV-CRM-001',
        'invoice_date': '2024-01-01',
        'total_amount': 1500,
        'items': [
            {'product': str(product.id), 'quantity': 15, 'price': 100, 'amount': 1500}
        ]
    }
    
    # We need to pass request to serializer context for user
    from django.test import RequestFactory
    request = RequestFactory().get('/')
    request.user = user
    
    serializer = SalesInvoiceSerializer(data=data, context={'request': request})
    
    if serializer.is_valid():
        print("❌ FAILED: Serializer validated successfully but should have failed due to credit limit!")
        # serializer.save()
    else:
        print("✅ SUCCESS: Serializer failed as expected.")
        print("Errors:", serializer.errors)
        
    # 4. Test PartyMeta (Loyalty)
    print("\n[Test 2] Party Metadata (Loyalty)")
    PartyMeta.objects.update_or_create(
        customer=customer,
        defaults={'loyalty_points': 500, 'party_category': 'wholesaler'}
    )
    
    # Verify via ORM
    cust_refresh = Customer.objects.get(id=customer.id)
    print(f"Loyalty Points: {cust_refresh.meta.loyalty_points}")
    print(f"Category: {cust_refresh.meta.party_category}")
    
    if cust_refresh.meta.loyalty_points == 500:
        print("✅ SUCCESS: PartyMeta persisted correctly.")
    else:
        print("❌ FAILED: PartyMeta mismatch.")

if __name__ == "__main__":
    run_test()

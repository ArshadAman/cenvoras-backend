from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from inventory.models import Product
from billing.models import Customer, SalesInvoice, SalesInvoiceItem, Payment, PaymentMode, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem
from ledger.models import Account, AccountType
import random
from datetime import timedelta, date
from decimal import Decimal

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds the database with realistic SME (Small Business) test data'

    def handle(self, *args, **kwargs):
        self.stdout.write('🌱 Seeding realistic SME data (Transaction-Based)...')
        
        # 0. CLEANUP: Deep Clean
        self.stdout.write('🧹 Wiping old data and resetting state...')
        
        # Delete transactions
        SalesInvoiceItem.objects.all().delete()
        SalesInvoice.objects.all().delete()
        SalesOrderItem.objects.all().delete()
        SalesOrder.objects.all().delete()
        DeliveryChallanItem.objects.all().delete()
        DeliveryChallan.objects.all().delete()
        Payment.objects.all().delete()
        
        from billing.models import PurchaseBill, PurchaseBillItem
        PurchaseBillItem.objects.all().delete()
        PurchaseBill.objects.all().delete()

        # Delete Inventory Data
        from inventory.models import Warehouse, ProductBatch, StockPoint
        StockPoint.objects.all().delete()
        ProductBatch.objects.all().delete()
        Warehouse.objects.all().delete()
        
        # Reset Products and Customers
        Product.objects.all().update(stock=0) # Reset to 0, let purchases build it up
        Customer.objects.all().update(current_balance=0)
        
        # 1. Get User
        user = User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR('No user found! Create a superuser first.'))
            return
            
        # 1.5 Create Warehouses
        main_warehouse, _ = Warehouse.objects.get_or_create(
            name="Main Warehouse",
            defaults={'address': '123 Industrial Area', 'created_by': user}
        )
        shop_floor, _ = Warehouse.objects.get_or_create(
            name="Shop Floor",
            defaults={'address': 'Retail Counter', 'created_by': user}
        )
        
        # 2. Ensure Products Exist (SME Context)
        products_data = [
            {'name': 'Tempered Glass (Generic)', 'price': 150, 'tax': 18},
            {'name': 'USB-C Cable (Fast)', 'price': 350, 'tax': 18},
            {'name': 'Micro USB Cable', 'price': 200, 'tax': 18},
            {'name': 'Earphones (Wired)', 'price': 450, 'tax': 18},
            {'name': 'Mobile Stand (Plastic)', 'price': 120, 'tax': 18},
            {'name': '20W Fast Charger', 'price': 850, 'tax': 18},
            {'name': 'Power Bank 10000mAh', 'price': 1600, 'tax': 18},
            {'name': 'Budget Smartphone 4G', 'price': 8500, 'tax': 12},
            {'name': 'Smart Watch (Entry)', 'price': 2500, 'tax': 18},
            {'name': 'SD Card 64GB', 'price': 650, 'tax': 18},
        ]
        
        db_products = []
        for p_data in products_data:
            product, _ = Product.objects.get_or_create(
                name=p_data['name'],
                created_by=user,
                defaults={
                    'sale_price': p_data['price'],
                    'price': p_data['price'] * 1.3,
                    'stock': 0, # Start at 0
                    'tax': p_data['tax']
                }
            )
            # Force reset if exists
            product.stock = 0
            product.sale_price = p_data['price']
            product.tax = p_data['tax']
            product.save()
            db_products.append(product)

            # Create Opening Stock Batch for every product
            batch, _ = ProductBatch.objects.get_or_create(
                product=product,
                batch_number="OPN-001",
                defaults={
                    'expiry_date': timezone.now().date() + timedelta(days=365),
                    'mrp': product.sale_price * Decimal('1.2'),
                    'sale_price': product.sale_price,
                    'cost_price': product.sale_price * Decimal('0.7'),
                }
            )
            # Add some stock to Main Warehouse
            StockPoint.objects.create(
                batch=batch,
                warehouse=main_warehouse,
                quantity=100
            )


        # 3. Ensure Customers Exist
        customer_names = [
            "Raju Bhai", "Anjali Ma'am", "Vikram Mechanic", "Sharma Ji", 
            "Student Hostel", "Local Gym", "Priya Salon", "Gupta Store",
            "Amit Kumar", "Neha Singh"
        ]
        
        db_customers = []
        categories = ['retailer', 'wholesaler', 'distributor', 'consumer']
        
        from billing.models_sidecar import PartyMeta

        for name in customer_names:
            cat = random.choice(categories)
            limit = random.choice([10000, 25000, 50000, 100000]) if cat != 'consumer' else 0
            
            customer, _ = Customer.objects.get_or_create(
                name=name,
                created_by=user,
                defaults={'phone': f"98765{random.randint(10000, 99999)}", 'current_balance': 0}
            )
            
            # Update Customer Fields
            customer.credit_limit = limit
            customer.current_balance = 0
            customer.save()
            
            # Create or Update PartyMeta
            PartyMeta.objects.update_or_create(
                customer=customer,
                defaults={
                    'party_category': cat,
                    'credit_days': 30 if cat != 'consumer' else 0,
                    'gst_type': 'regular' if cat in ['distributor', 'wholesaler'] else 'unregistered'
                }
            )
            
            db_customers.append(customer)

        # 3.5 Create Price Lists and Schemes (New)
        from inventory.models_pricing import PriceList, PriceListItem, Scheme
        PriceList.objects.all().delete()
        Scheme.objects.all().delete()
        
        today = date.today()
        # Wholesale Price List
        pl_wholesale = PriceList.objects.create(
            name="Wholesale Standard",
            party_category="wholesaler",
            is_active=True,
            created_by=user
        )
        for prod in db_products:
            PriceListItem.objects.create(
                price_list=pl_wholesale,
                product=prod,
                price=prod.sale_price * Decimal('0.85'), # 15% cheaper
                min_qty=10
            )
            
        # Retail Scheme (Buy 2 Get 1)
        scheme_prod = db_products[0] # Tempered Glass
        Scheme.objects.create(
            name="Tempered Glass BOGO",
            scheme_type="bogo",
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
            product=scheme_prod,
            free_product=scheme_prod,
            min_qty=2,
            free_qty=1,
            is_active=True,
            created_by=user
        )
            
        # 4. Chronological Seeding Review (Purchases -> Sales)
        # We process day by day from -90 days to today.
        
        today = date.today()
        vendors = ["Global Distributors", "City Wholesalers", "TechSupply India"]
        
        total_purchases = 0
        total_invoices = 0
        total_orders = 0
        total_challans = 0
        
        for i in range(90, -1, -1): # 90 days history
            day = today - timedelta(days=i)
            is_weekend = day.weekday() in [5, 6]
            
            # --- A. PURCHASES (Restocking) ---
            # Buying happens if stock is low or randomly
            # For simplicity, random restocks + initial heavy stock
            
            # Day 0 (90 days ago): Big initial stock up
            if i == 90 or random.random() < 0.1: # Initial or 10% chance
                vendor = random.choice(vendors)
                bill = PurchaseBill.objects.create(
                    vendor_name=vendor,
                    bill_number=f"PB-{random.randint(10000, 99999)}",
                    bill_date=day,
                    created_by=user,
                    total_amount=0
                )
                
                bill_total = 0
                # Buy random subset of products
                for prod in random.sample(db_products, k=random.randint(3, 8)):
                    qty = random.randint(20, 100) # Bulk buy
                    cost = int(float(prod.sale_price) * 0.7) # 30% margin
                    
                    taxable = cost * qty
                    tax_amt = taxable * float(prod.tax or 18) / 100
                    line_total = taxable + tax_amt
                    
                    PurchaseBillItem.objects.create(
                        purchase_bill=bill,
                        product=prod,
                        quantity=qty,
                        unit="pcs",
                        price=cost,
                        tax=prod.tax,
                        amount=line_total
                    )
                    bill_total += line_total
                    # Signal automatically updates Product.stock here
                
                bill.total_amount = bill_total
                bill.save()
                total_purchases += 1
                
            # --- B. SALES (Selling from stock) ---
            if random.random() < 0.05: continue # 5% closed days
            
            
            # --- C. SALES ORDERS (New Feature) ---
            if random.random() < 0.2: # 20% chance of an order coming in
                cust = random.choice(db_customers)
                order_total = 0
                order_items = []
                
                for _ in range(random.randint(1, 3)):
                    prod = random.choice(db_products)
                    qty = random.randint(5, 20) # Bulk order
                    price = prod.sale_price
                    amount = price * qty
                    
                    order_items.append({
                        'product': prod,
                        'quantity': qty,
                        'price': price,
                        'amount': amount
                    })
                    order_total += amount
                
                if order_items:
                    so = SalesOrder.objects.create(
                        order_number=f"SO-{day.strftime('%Y%m%d')}-{random.randint(100, 999)}",
                        date=day,
                        customer=cust,
                        total_amount=order_total,
                        stage=random.choice(['new', 'packed', 'shipped', 'completed']),
                        created_by=user
                    )
                    
                    for item in order_items:
                        SalesOrderItem.objects.create(
                            order=so,
                            **item
                        )
                    total_orders += 1

            
            # --- D. DELIVERY CHALLANS (New Feature) ---
            if random.random() < 0.15: # 15% chance of a delivery challan
                 cust = random.choice(db_customers)
                 challan_items_data = []
                 
                 for _ in range(random.randint(1, 4)):
                    prod = random.choice(db_products)
                    # Check stock for challan too, as it reduces stock
                    prod.refresh_from_db()
                    if prod.stock <= 5: continue
                    
                    qty = random.randint(2, 10)
                    if prod.stock < qty: qty = prod.stock
                    
                    challan_items_data.append({
                        'product': prod,
                        'quantity': qty
                    })
                 
                 if challan_items_data:
                     dc = DeliveryChallan.objects.create(
                        challan_number=f"DC-{day.strftime('%Y%m%d')}-{random.randint(100, 999)}",
                        date=day,
                        customer=cust,
                        is_billed=random.choice([True, False]),
                        created_by=user
                     )
                     
                     for item in challan_items_data:
                         DeliveryChallanItem.objects.create(
                             challan=dc,
                             **item
                         )
                         # Note: Stock reduction logic should be in models/signals. 
                         # If not implemented there, we might need to manually reduce stock here for realism.
                         # Assuming signal or manual reduction:
                         item['product'].stock -= item['quantity']
                         item['product'].save()
                         
                     total_challans += 1


            # --- E. INVOICES (Regular Sales) ---
            num_sales = random.randint(5, 15) if is_weekend else random.randint(3, 8)
            
            for _ in range(num_sales):
                cust = random.choice(db_customers)
                
                # Pre-calculate items to ensure Invoice is created with correct Total
                # This ensures the 'post_save' signal (if created) adds correct debt to Customer
                proposed_items = []
                inv_total = 0
                
                for _ in range(random.randint(1, 3)):
                    prod = random.choice(db_products)
                    
                    # Check Real Stock
                    prod.refresh_from_db()
                    if prod.stock <= 0: continue
                    
                    qty = random.randint(1, 2)
                    if prod.stock < qty: qty = prod.stock # Sell only what we have
                    
                    amount = prod.sale_price * qty
                    
                    proposed_items.append({
                        'product': prod,
                        'qty': qty,
                        'amount': amount,
                        'tax': prod.tax
                    })
                    inv_total += amount
                
                if not proposed_items:
                    continue
                    
                # Create Invoice with Total (Triggers Balance Update Signal)
                inv = SalesInvoice.objects.create(
                    customer=cust,
                    customer_name=cust.name,
                    invoice_number=f"INV-{day.strftime('%Y%m%d')}-{random.randint(1000, 9999)}",
                    invoice_date=day,
                    created_by=user,
                    total_amount=inv_total
                )
                
                # Create Items
                for item in proposed_items:
                    SalesInvoiceItem.objects.create(
                        sales_invoice=inv,
                        product=item['product'],
                        quantity=item['qty'],
                        unit="pcs",
                        price=item['product'].sale_price,
                        tax=item['tax'],
                        amount=item['amount']
                    )
                    # Signal automatically reduces Product.stock here

                total_invoices += 1
                
                # --- F. PAYMENTS ---
                # 70% Cash/UPI (Immediate), 30% Credit (Udhaar)
                payment_mode = random.choices(['immediate', 'credit'], weights=[0.7, 0.3])[0]
                
                if payment_mode == 'immediate':
                    mode = random.choice([PaymentMode.CASH, PaymentMode.UPI])
                    Payment.objects.create(
                        customer=cust,
                        date=day,
                        amount=inv_total,
                        mode=mode,
                        created_by=user
                    )
                    # Signal reduces Customer Balance here
                
                # If credit, balance remains high (Udhaar)
                
                
        self.stdout.write(self.style.SUCCESS(f'✨ Transaction-based seed complete!'))
        self.stdout.write(f'Bills: {total_purchases}, Invoices: {total_invoices}, Orders: {total_orders}, Challans: {total_challans}')

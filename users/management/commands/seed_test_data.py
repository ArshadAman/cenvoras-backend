from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction
import random
from datetime import timedelta, date
from decimal import Decimal

from inventory.models import Product, Warehouse, ProductBatch, StockPoint
from billing.models import Customer, PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Payment
from ledger.services import AccountingService
from ledger.models import GeneralLedgerEntry

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds the database with test data for testuser@example.com'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding test data...')
        
        try:
            with transaction.atomic():
                self.seed_data()
                self.stdout.write(self.style.SUCCESS('Successfully seeded test data!'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error seeding data: {str(e)}'))
            import traceback
            traceback.print_exc()

    def seed_data(self):
        # 1. Get the SPECIFIC user the user is complaining about
        try:
            user = User.objects.get(username='testuser@example.com')
            self.stdout.write(f'Found specific user: {user.username} (ID: {user.id})')
        except User.DoesNotExist:
            self.stdout.write('User "testuser@example.com" not found by username. Trying email...')
            # Prioritize the one with matching username if possible, otherwise first match
            user = User.objects.filter(email='testuser@example.com').first()
            
        if not user:
            # Fallback create if absolutely no one exists
            self.stdout.write('Creating new user as last resort...')
            phone = '9876543210'
            if User.objects.filter(phone=phone).exists():
                phone = f'9{random.randint(100000000, 999999999)}'
                
            user = User.objects.create(
                username='testuser@example.com',
                email='testuser@gmail.com',
                business_name='My Business Name',
                phone=phone,
                gstin='29ABCDE1234F1Z5',
                business_address='123 Test Street, Tech Park, Bangalore',
                state='29',
                is_staff=True,
                is_superuser=True
            )
            user.set_password('password123')
            user.save()

        # Update business name match
        if user.business_name != 'My Test Shop':
            user.business_name = 'My Test Shop'
            user.save()
            
        # CLEANUP EXISTING DATA
        self.stdout.write(f'Cleaning up existing data for {user.username}...')
        
        # IMPORTANT: Delete ALL SalesInvoices associated with this user's customers OR created by this user
        # This handles the case where user created invoices linked to their customers
        # AND cases where dependent objects might block customer deletion
        
        # 1. Delete invoices created by user
        SalesInvoice.objects.filter(created_by=user).delete()
        
        # 2. Delete invoices linked to user's customers (even if created by others, though unlikely)
        # We must filter by customers owned by this user
        user_customers = Customer.objects.filter(created_by=user)
        SalesInvoice.objects.filter(customer__in=user_customers).delete()

        # 3. Proceed with other deletions
        PurchaseBill.objects.filter(created_by=user).delete()
        Product.objects.filter(created_by=user).delete()
        
        # Now safe to delete customers
        Customer.objects.filter(created_by=user).delete()
        
        Warehouse.objects.filter(created_by=user).delete()
        GeneralLedgerEntry.objects.filter(created_by=user).delete()
        Payment.objects.filter(created_by=user).delete()
        self.stdout.write('Cleanup complete.')

        # 2. Initialize Accounting
        AccountingService.get_or_create_default_accounts(user)
        self.stdout.write('Initialized Chart of Accounts')

        # 3. Create Warehouses
        warehouses = []
        warehouse_names = ['Main Warehouse', 'North Godown', 'South Depot']
        for name in warehouse_names:
            wh, _ = Warehouse.objects.get_or_create(
                name=name,
                created_by=user,
                defaults={'address': f'{name} Address, Industrial Area'}
            )
            warehouses.append(wh)
        self.stdout.write(f'Created {len(warehouses)} Warehouses')

        # 4. Create Customers
        customers = []
        customer_data = [
            ('Alpha Traders', '29XYZAB1234C1Z1'),
            ('Beta Retailers', '29PQRST5678D1Z2'),
            ('Gamma Solutions', None), # Unregistered
            ('Delta Distributors', '29LMNO9012E1Z3'),
            ('Epsilon Enterprises', '27ABCDE1234F1Z5'), # Maharashtra (IGST)
            ('Zeta Corp', '29FGHIJ3456G1Z6'), 
            ('Eta Electronics', None),
            ('Theta Technologies', '29KLMNO7890H1Z7'),
            ('Iota Industries', '33ABCDE1234F1Z5'), # Tamil Nadu (IGST)
            ('Kappa Kings', '29PQRST1234I1Z8'),
            ('Lambda Logistics', '29UVWXY5678J1Z9'),
            ('Mu Manufacturers', '29ABCDE9012K1Z0'),
            ('Nu Networks', None),
            ('Xi Xylophone', '29FGHIJ3456L1Z1'),
            ('Omicron Outlets', '29KLMNO7890M1Z2'),
        ]
        
        for name, gstin in customer_data:
            cust, _ = Customer.objects.get_or_create(
                name=name,
                created_by=user,
                defaults={
                    'email': f'contact@{name.lower().replace(" ", "")}.com',
                    'phone': f'9{random.randint(100000000, 999999999)}',
                    'gstin': gstin,
                    'address': f'Address of {name}',
                    'state': gstin[:2] if gstin else '29', # Default Karnataka if no GSTIN
                    'credit_limit': 100000,
                    'allow_credit': True
                }
            )
            customers.append(cust)
        self.stdout.write(f'Created {len(customers)} Customers')
        
        # 5. Create Vendors 
        vendors = [
            {'name': 'Global Suppliers', 'gstin': '29AAAAA1111A1Z1', 'state': '29'},
            {'name': 'Tech Importers', 'gstin': '27BBBBB2222B1Z2', 'state': '27'}, 
            {'name': 'Local Wholesalers', 'gstin': '29CCCCC3333C1Z3', 'state': '29'},
            {'name': 'Quality Goods', 'gstin': '29DDDDD4444D1Z4', 'state': '29'},
            {'name': 'Best Components', 'gstin': '33EEEEE5555E1Z5', 'state': '33'},
        ]

        # 6. Create Products
        products = []
        product_categories = [
            ('Smart Phone X', 15000, 18000),
            ('Laptop Pro', 55000, 65000),
            ('Wireless Earbuds', 2000, 3500),
            ('USB-C Cable', 150, 400),
            ('Power Bank 10000mAh', 800, 1200),
            ('Gaming Mouse', 1500, 2500),
            ('Mechanical Keyboard', 3000, 4500),
            ('27 Inch Monitor', 12000, 16000),
            ('Graphic Card RTX', 35000, 42000),
            ('RAM 16GB', 3500, 4500),
            ('SSD 1TB', 4500, 6000),
            ('CPU Cooler', 2500, 3500),
            ('Cabinet Case', 4000, 5500),
            ('Power Supply 650W', 3500, 4500),
            ('Webcam HD', 2000, 3000),
            ('Microphone USB', 4000, 5500),
            ('Headset 7.1', 2500, 3500),
            ('Router WiFi 6', 3000, 4500),
            ('Switch 8-Port', 1200, 1800),
            ('Server Rack', 15000, 20000),
        ]

        for name, cost, price in product_categories:
            prod, _ = Product.objects.get_or_create(
                name=name,
                created_by=user,
                defaults={
                    'hsn_sac_code': str(random.randint(8500, 8599)),
                    'description': f'Description for {name}',
                    'unit': 'pcs',
                    'price': cost,  
                    'sale_price': price,
                    'tax': 18.00,
                    'low_stock_alert': 10
                }
            )
            products.append(prod)
        self.stdout.write(f'Created {len(products)} Products')

        # 7. Create Purchase Bills (To bring stock IN)
        self.stdout.write('Creating Purchase Bills...')
        today = date.today()
        
        batches = []

        for i in range(20):
            vendor = random.choice(vendors)
            bill_date = today - timedelta(days=random.randint(10, 60))
            
            bill = PurchaseBill.objects.create(
                bill_number=f'PB-{2024}-{i+100}',
                bill_date=bill_date,
                due_date=bill_date + timedelta(days=30),
                vendor_name=vendor['name'],
                vendor_gstin=vendor['gstin'],
                vendor_address=f"Address of {vendor['name']}",
                # place_of_supply removed as it's not in the model
                gst_treatment='Regular' if vendor['gstin'] else 'Unregistered',
                warehouse=random.choice(warehouses),
                total_amount=0, # Will calculate
                created_by=user
            )
            
            # Add items to bill
            bill_total = Decimal('0')
            num_items = random.randint(3, 8)
            selected_products = random.sample(products, num_items)
            
            for prod in selected_products:
                qty = random.randint(10, 100)
                rate = prod.price # Cost price
                amount = Decimal(qty) * rate
                tax = amount * Decimal('0.18')
                total_line = amount + tax
                
                # Create Batch for this purchase
                batch_no = f"B-{bill.bill_number}-{prod.id.hex[:4].upper()}"
                batch = ProductBatch.objects.create(
                    product=prod,
                    batch_number=batch_no,
                    expiry_date=today + timedelta(days=random.randint(180, 720)),
                    manufacturing_date=bill_date - timedelta(days=10),
                    mrp=prod.sale_price * Decimal('1.2'),
                    cost_price=rate,
                    sale_price=prod.sale_price
                )
                batches.append(batch)

                PurchaseBillItem.objects.create(
                    purchase_bill=bill,
                    product=prod,
                    batch=batch,
                    quantity=qty,
                    unit=prod.unit,
                    price=rate,
                    tax=tax,
                    amount=total_line
                )
                bill_total += total_line

            bill.total_amount = bill_total
            bill.save()
        
        self.stdout.write('Created 20 Purchase Bills and Stocks')

        # 8. Create Sales Invoices (To move stock OUT)
        self.stdout.write('Creating Sales Invoices...')
        for i in range(30):
            customer = random.choice(customers)
            invoice_date = today - timedelta(days=random.randint(0, 30))
            
            invoice = SalesInvoice.objects.create(
                customer=customer,
                customer_name=customer.name,
                invoice_number=f'INV-{2024}-{i+1000}',
                invoice_date=invoice_date,
                due_date=invoice_date + timedelta(days=15),
                place_of_supply=customer.state,
                gst_treatment='Regular' if customer.gstin else 'Consumer',
                warehouse=random.choice(warehouses), 
                total_amount=0,
                created_by=user
            )

            # Add items
            inv_total = Decimal('0')
            num_items = random.randint(1, 5)
            selected_products = random.sample(products, num_items)
            
            for prod in selected_products:
                prod_batches = [b for b in batches if b.product == prod]
                if not prod_batches: continue
                
                batch = random.choice(prod_batches)
                qty = random.randint(1, 10)
                
                rate = prod.sale_price
                amount = Decimal(qty) * rate
                tax = amount * Decimal('0.18')
                total_line = amount + tax

                SalesInvoiceItem.objects.create(
                    sales_invoice=invoice,
                    product=prod,
                    batch=batch,
                    quantity=qty,
                    unit=prod.unit,
                    price=rate,
                    tax=tax,
                    amount=total_line
                )
                inv_total += total_line
            
            invoice.total_amount = inv_total
            invoice.save()

            # Record a random payment for some invoices
            if random.choice([True, False]):
                Payment.objects.create(
                    customer=customer,
                    date=invoice_date + timedelta(days=random.randint(0, 5)),
                    amount=inv_total, # Full payment
                    mode='upi',
                    reference=f'UPI-{random.randint(10000,99999)}',
                    created_by=user
                )

        self.stdout.write('Created 30 Sales Invoices and random Payments')
        self.stdout.write('Seed completed successfully.')

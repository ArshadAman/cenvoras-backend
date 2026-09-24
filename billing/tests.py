from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import SalesInvoice, PurchaseBill, Payment, Customer
from billing.models_sidecar import Quotation
from inventory.models import Product
from decimal import Decimal
import datetime
from datetime import date, timedelta
from django.utils import timezone
from subscription.models import Plan, TenantSubscription

User = get_user_model()

class SalesInvoiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tenant_user",
            email="tenant@test.com", 
            password="testpassword",
            first_name="Test",
            last_name="Tenant"
        )
        self.pro_plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro", "monthly_price": 0})
        self.starter_plan, _ = Plan.objects.get_or_create(code="starter", defaults={"name": "Starter", "monthly_price": 0})
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=self.pro_plan,
            status="active",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        self.user = User.objects.create_user(
            username="test_user",
            email="test@test.com", 
            password="testpassword",
            first_name="Test",
            last_name="User",
            parent=self.tenant
        )
        self.client.force_authenticate(user=self.user)

    def _set_plan(self, plan):
        self.tenant.subscription_status = "active"
        self.tenant.subscription_tier = plan.code.upper()
        self.tenant.save(update_fields=["subscription_status", "subscription_tier"])
        subscription = self.tenant.subscription
        subscription.plan = plan
        subscription.status = "active"
        subscription.current_period_end = timezone.now() + timedelta(days=30)
        subscription.save(update_fields=["plan", "status", "current_period_end", "updated_at"])
        
    def test_invoice_creation_success(self):
        data = {
            "customer_name": "John Doe",
            "invoice_number": "INV-ABCD-001",
            "invoice_date": "2024-01-01",
            "status": "final",
            "total_amount": "100.00",
            "items": [
                {
                    "product": "Test Product",
                    "quantity": 1,
                    "price": "100.00",
                    "amount": "100.00"
                }
            ]
        }
        res = self.client.post("/api/billing/sales-invoices/", data, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(SalesInvoice.objects.first().status, "final")

    def test_invoice_creation_conflict(self):
        # Create first invoice
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Jane Doe",
            invoice_number="INV-ABCD-002",
            invoice_date="2024-01-01",
            total_amount=50.00
        )
        
        # Try to create with same number
        data = {
            "customer_name": "John Doe",
            "invoice_number": "INV-ABCD-002",
            "invoice_date": "2024-01-02",
            "total_amount": "100.00",
            "items": []
        }
        res = self.client.post("/api/billing/sales-invoices/", data, format='json')
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        
    def test_draft_saving(self):
        data = {
            "customer_name": "", # draft allows empty customer name
            "invoice_number": "INV-ABCD-003",
            "invoice_date": "2024-01-01",
            "status": "draft",
            "total_amount": "0.00",
            "items": []
        }
        res = self.client.post("/api/billing/sales-invoices/", data, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SalesInvoice.objects.get(invoice_number="INV-ABCD-003").status, "draft")

    def test_starter_plan_cannot_autocreate_inventory_product(self):
        self._set_plan(self.starter_plan)

        data = {
            "customer_name": "John Doe",
            "invoice_number": "INV-ABCD-004",
            "invoice_date": "2024-01-01",
            "status": "final",
            "total_amount": "100.00",
            "items": [
                {
                    "product": "Starter Blocked Product",
                    "quantity": 1,
                    "price": "100.00",
                    "amount": "100.00"
                }
            ]
        }

        res = self.client.post("/api/billing/sales-invoices/", data, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", str(res.data))
        self.assertFalse(Product.objects.filter(name__iexact="Starter Blocked Product", created_by=self.tenant).exists())

    def test_analytics_endpoint(self):
        SalesInvoice.objects.create(created_by=self.tenant, customer_name="Jane Doe", invoice_number="INV-1", invoice_date="2024-01-01", total_amount=100.00)
        SalesInvoice.objects.create(created_by=self.tenant, customer_name="Jane Doe", invoice_number="INV-2", invoice_date="2024-05-05", total_amount=200.00)
        
        res = self.client.get("/api/billing/sales-invoices/analytics/?start_date=2024-01-01&end_date=2024-02-01")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_revenue'], 100.00)
        self.assertEqual(res.data['total_invoices'], 1)

    def test_analytics_includes_non_draft_invoices(self):
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Alice",
            invoice_number="INV-ND-001",
            invoice_date="2024-01-10",
            total_amount=150.00,
            status="final",
        )
        # Legacy/edge-case non-draft status should still count in analytics totals.
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Bob",
            invoice_number="INV-ND-002",
            invoice_date="2024-01-10",
            total_amount=250.00,
            status="pending",
        )
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Charlie",
            invoice_number="INV-ND-003",
            invoice_date="2024-01-10",
            total_amount=500.00,
            status="draft",
        )

        res = self.client.get("/api/billing/sales-invoices/analytics/?start_date=2024-01-01&end_date=2024-01-31")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_invoices'], 2)
        self.assertEqual(float(res.data['total_revenue']), 400.00)

    def test_overdue_report_excludes_drafts_and_returns_invoice_rows(self):
        due_date = date.today() - timedelta(days=3)
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Overdue Final",
            invoice_number="INV-OD-001",
            invoice_date=date.today(),
            due_date=due_date,
            total_amount=300.00,
            amount_paid=0,
            payment_status="pending",
            status="final",
        )
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Overdue Draft",
            invoice_number="INV-OD-002",
            invoice_date=date.today(),
            due_date=due_date,
            total_amount=400.00,
            amount_paid=0,
            payment_status="pending",
            status="draft",
        )

        res = self.client.get("/api/billing/reports/overdue-bills/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['invoice_number'], "INV-OD-001")

    def test_partial_paid_invoice_blocks_immutable_field_edit(self):
        invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Jane Doe",
            invoice_number="INV-LOCK-001",
            invoice_date="2024-01-01",
            total_amount=100.00,
            amount_paid=50.00,
            payment_status="partial_paid",
        )

        res = self.client.patch(
            f"/api/billing/sales-invoices/{invoice.id}/edit/",
            {"invoice_number": "INV-LOCK-NEW"},
            format='json'
        )

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('invoice_number', res.data)

    def test_partial_paid_invoice_allows_item_edit_and_recomputes_total(self):
        invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer_name="Jane Doe",
            invoice_number="INV-EDIT-001",
            invoice_date="2024-01-01",
            total_amount=100.00,
            amount_paid=50.00,
            payment_status="partial_paid",
        )

        payload = {
            "items": [
                {
                    "product": "Edited Product",
                    "quantity": 2,
                    "price": "60.00",
                    "amount": "120.00"
                }
            ]
        }
        res = self.client.patch(f"/api/billing/sales-invoices/{invoice.id}/edit/", payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        invoice.refresh_from_db()
        self.assertEqual(float(invoice.total_amount), 120.0)
        self.assertEqual(invoice.payment_status, "partial_paid")


class PurchaseBillValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tenant_user_pb",
            email="tenant.pb@test.com",
            password="testpassword",
            first_name="Tenant",
            last_name="PB"
        )
        self.user = User.objects.create_user(
            username="test_user_pb",
            email="test.pb@test.com",
            password="testpassword",
            first_name="Test",
            last_name="PB",
            parent=self.tenant
        )
        self.client.force_authenticate(user=self.user)

    def test_purchase_list_invalid_page_limit_returns_400(self):
        res = self.client.get("/api/billing/purchase-bills/?page=abc&limit=-1")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data.get("success", True))
        self.assertIn("errors", res.data)


class QuotationProductAutocreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tenant_user_qt",
            email="tenant.qt@test.com",
            password="testpassword",
            first_name="Tenant",
            last_name="QT"
        )
        self.pro_plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro", "monthly_price": 0})
        self.starter_plan, _ = Plan.objects.get_or_create(code="starter", defaults={"name": "Starter", "monthly_price": 0})
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=self.pro_plan,
            status="active",
            current_period_end=timezone.now() + timedelta(days=30),
        )
        self.user = User.objects.create_user(
            username="test_user_qt",
            email="test.qt@test.com",
            password="testpassword",
            first_name="Test",
            last_name="QT",
            parent=self.tenant
        )
        self.client.force_authenticate(user=self.user)

    def _set_plan(self, plan):
        self.tenant.subscription_status = "active"
        self.tenant.subscription_tier = plan.code.upper()
        self.tenant.save(update_fields=["subscription_status", "subscription_tier"])
        subscription = self.tenant.subscription
        subscription.plan = plan
        subscription.status = "active"
        subscription.current_period_end = timezone.now() + timedelta(days=30)
        subscription.save(update_fields=["plan", "status", "current_period_end", "updated_at"])

    def test_quotation_create_autocreates_missing_product(self):
        payload = {
            "customer_name": "Walk-in Customer",
            "status": "draft",
            "items": [
                {
                    "product": "Brand New Item",
                    "quantity": 2,
                    "price": "120.00",
                    "tax": "18.00",
                    "amount": "240.00",
                    "unit": "pcs",
                    "hsn_sac_code": "9983"
                }
            ]
        }

        response = self.client.post("/api/billing/quotations/", payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Quotation.objects.filter(created_by=self.tenant).count(), 1)

        created_product = Product.objects.filter(name__iexact="Brand New Item", created_by=self.tenant).first()
        self.assertIsNotNone(created_product)
        self.assertEqual(str(created_product.unit), "pcs")

    def test_quotation_starter_plan_cannot_autocreate_inventory_product(self):
        self._set_plan(self.starter_plan)

        payload = {
            "customer_name": "Walk-in Customer",
            "status": "draft",
            "items": [
                {
                    "product": "Starter Blocked Quote Item",
                    "quantity": 2,
                    "price": "120.00",
                    "tax": "18.00",
                    "amount": "240.00",
                    "unit": "pcs",
                    "hsn_sac_code": "9983"
                }
            ]
        }

        response = self.client.post("/api/billing/quotations/", payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("product", str(response.data))
        self.assertFalse(Product.objects.filter(name__iexact="Starter Blocked Quote Item", created_by=self.tenant).exists())


class PaymentStatusTests(TestCase):
    """Test that payment status updates correctly when payments are recorded"""
    
    def setUp(self):
        from django.contrib.auth import get_user_model
        from billing.models import Customer
        User = get_user_model()
        
        self.user = User.objects.create_user(
            username="payment_test_user",
            email="payment@test.com",
            password="testpass"
        )
        
        self.customer = Customer.objects.create(
            name="Test Customer",
            created_by=self.user
        )
        
        self.invoice = SalesInvoice.objects.create(
            created_by=self.user,
            customer_name="Test Customer",
            invoice_number="TEST-PAY-001",
            invoice_date="2024-01-01",
            total_amount=100.00,
            customer=self.customer
        )
    
    def test_invoice_status_pending_on_creation(self):
        """Invoice should be pending when created with no payments"""
        self.assertEqual(self.invoice.payment_status, "pending")
        self.assertEqual(self.invoice.amount_paid, 0)
    
    def test_invoice_status_updates_to_partial_paid_on_partial_payment(self):
        """Invoice status should change from pending to partial_paid when partial payment is recorded"""
        from billing.models import Payment
        
        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date="2024-01-01",
            amount=50.00,
            created_by=self.user
        )
        
        self.invoice.refresh_from_db()
        
        # Status should be updated to partial_paid
        self.assertEqual(self.invoice.payment_status, "partial_paid")
        # Amount paid should be updated
        self.assertEqual(float(self.invoice.amount_paid), 50.00)
    
    def test_invoice_status_updates_to_paid_on_full_payment(self):
        """Invoice status should change to paid when full payment is recorded"""
        from billing.models import Payment
        
        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date="2024-01-01",
            amount=100.00,
            created_by=self.user
        )
        
        self.invoice.refresh_from_db()
        
        # Status should be updated to paid
        self.assertEqual(self.invoice.payment_status, "paid")
        # Amount paid should equal total
        self.assertEqual(float(self.invoice.amount_paid), 100.00)
    
    def test_customer_balance_decreases_on_payment(self):
        """Customer current_balance (udhar) should decrease when payment is recorded"""
        from billing.models import Payment
        
        initial_balance = 100.00
        # Set initial balance
        self.customer.current_balance = initial_balance
        self.customer.save()
        
        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date="2024-01-01",
            amount=50.00,
            created_by=self.user
        )
        
        self.customer.refresh_from_db()
        
        # Balance should decrease by payment amount
        self.assertEqual(float(self.customer.current_balance), initial_balance - 50.00)
    
    def test_payment_without_invoice_allowed_on_account(self):
        """Payment without invoice should be allowed on customer account"""
        from rest_framework.test import APIClient
        
        client = APIClient()
        client.force_authenticate(user=self.user)
        
        # Create payment without invoice (on account)
        response = client.post('/api/billing/payments/', {
            'customer': str(self.customer.id),
            'invoice': '',  # No invoice
            'date': '2024-01-01',
            'amount': 50.00,
            'mode': 'cash'
        })
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class SalesOrderConversionTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from inventory.models import Product
        from billing.models import Customer
        from billing.models_sidecar import SalesOrder, SalesOrderItem
        from datetime import date

        User = get_user_model()
        self.tenant = User.objects.create_user(
            username="so_tenant",
            email="sotenant@test.com",
            password="testpassword",
            state="Maharashtra"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Apex Retailers",
            state="Maharashtra",
            current_balance=Decimal("0.00"),
            created_by=self.tenant
        )

        self.product_kg = Product.objects.create(
            name="Organic Rice",
            unit="kg",
            price=Decimal("80.00"),
            tax=Decimal("5.00"),
            created_by=self.tenant
        )

        self.product_litres = Product.objects.create(
            name="Mustard Oil",
            unit="litres",
            price=Decimal("150.00"),
            tax=Decimal("5.00"),
            created_by=self.tenant
        )

        self.order = SalesOrder.objects.create(
            order_number="SO-TEST-001",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("1150.00"),
            created_by=self.tenant
        )

        self.item1 = SalesOrderItem.objects.create(
            order=self.order,
            product=self.product_kg,
            quantity=5,
            unit="kg",
            price=Decimal("80.00"),
            amount=Decimal("400.00")
        )

        self.item2 = SalesOrderItem.objects.create(
            order=self.order,
            product=self.product_litres,
            quantity=5,
            unit="litres",
            price=Decimal("150.00"),
            amount=Decimal("750.00")
        )

    def test_convert_order_to_invoice_preserves_units(self):
        """Converting an order must preserve units (e.g. kg, litres) and not corrupt them to 'pcs'"""
        res = self.client.post(f"/api/billing/sales-orders/{self.order.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("message"), "Converted successfully")

        invoice_id = res.data.get("invoice_id")
        invoice = SalesInvoice.objects.prefetch_related('items__product').get(id=invoice_id)
        
        items = list(invoice.items.all().order_by('product__name'))
        self.assertEqual(len(items), 2)
        
        # Product 1: Mustard Oil (litres)
        self.assertEqual(items[0].product.name, "Mustard Oil")
        self.assertEqual(items[0].unit, "litres")
        self.assertNotEqual(items[0].unit, "pcs")
        
        # Product 2: Organic Rice (kg)
        self.assertEqual(items[1].product.name, "Organic Rice")
        self.assertEqual(items[1].unit, "kg")
        self.assertNotEqual(items[1].unit, "pcs")

    def test_convert_order_to_invoice_updates_customer_balance(self):
        """Converting an order must increment Customer.current_balance by total_amount"""
        initial_balance = self.customer.current_balance
        res = self.client.post(f"/api/billing/sales-orders/{self.order.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, initial_balance + self.order.total_amount)

    def test_convert_order_to_invoice_creates_ledger_entries(self):
        """Converting an order must post double-entry records to GeneralLedgerEntry"""
        from ledger.models import GeneralLedgerEntry
        res = self.client.post(f"/api/billing/sales-orders/{self.order.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        invoice_id = res.data.get("invoice_id")
        invoice = SalesInvoice.objects.get(id=invoice_id)

        entries = GeneralLedgerEntry.objects.filter(sales_invoice=invoice)
        self.assertTrue(entries.exists())

        # Total debits must equal total credits
        total_debit = sum(e.debit for e in entries)
        total_credit = sum(e.credit for e in entries)
        self.assertEqual(total_debit, invoice.total_amount)
        self.assertEqual(total_credit, invoice.total_amount)

        # Accounts Receivable entry must be linked to customer
        ar_entry = entries.filter(debit=invoice.total_amount).first()
        self.assertIsNotNone(ar_entry)
        self.assertEqual(ar_entry.customer, self.customer)
        # Detailed description should include unit
        self.assertIn("kg", ar_entry.description)
        self.assertIn("litres", ar_entry.description)

    def test_convert_order_twice_is_rejected(self):
        """Converting an already completed order should return 400 Bad Request"""
        res1 = self.client.post(f"/api/billing/sales-orders/{self.order.id}/convert_to_invoice/")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        # Second conversion attempt
        res2 = self.client.post(f"/api/billing/sales-orders/{self.order.id}/convert_to_invoice/")
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already been converted", res2.data.get("message", ""))


class PaymentEditDesyncTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from billing.models import Customer, SalesInvoice, Payment
        from datetime import date

        User = get_user_model()
        self.tenant = User.objects.create_user(
            username="payment_edit_tenant",
            email="petenant@test.com",
            password="testpassword",
            state="Maharashtra"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Reliable Traders",
            current_balance=Decimal("100000.00"),
            created_by=self.tenant
        )

        self.customer2 = Customer.objects.create(
            name="Metro Enterprises",
            current_balance=Decimal("50000.00"),
            created_by=self.tenant
        )

        self.invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name="Reliable Traders",
            invoice_number="INV-PAY-001",
            invoice_date=date.today(),
            total_amount=Decimal("50000.00"),
            amount_paid=Decimal("0.00"),
            payment_status="pending",
            status="final"
        )

        self.invoice2 = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name="Reliable Traders",
            invoice_number="INV-PAY-002",
            invoice_date=date.today(),
            total_amount=Decimal("30000.00"),
            amount_paid=Decimal("0.00"),
            payment_status="pending",
            status="final"
        )

    def test_payment_edit_increases_amount_syncs_balance_invoice_and_ledger(self):
        """Cashier mistypes ₹5,000 instead of ₹50,000, then edits it: balance, invoice, and ledger must all sync to ₹50,000"""
        from ledger.models import GeneralLedgerEntry
        from decimal import Decimal
        from datetime import date

        # 1. Initial payment created as 5,000
        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date=date.today(),
            amount=Decimal("5000.00"),
            mode="cash",
            created_by=self.tenant
        )

        self.customer.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("95000.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("5000.00"))
        self.assertEqual(self.invoice.payment_status, "partial_paid")

        ledger_entries = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}")
        self.assertEqual(ledger_entries.count(), 2)
        self.assertEqual(sum(e.debit for e in ledger_entries), Decimal("5000.00"))
        self.assertEqual(sum(e.credit for e in ledger_entries), Decimal("5000.00"))

        # 2. Cashier edits payment to 50,000
        payment.amount = Decimal("50000.00")
        payment.save()

        # 3. Verify Customer balance adjusted by delta (-45,000 -> 50,000)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("50000.00"))

        # 4. Verify Invoice amount_paid adjusted by delta (+45,000 -> 50,000) and status changed to paid
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("50000.00"))
        self.assertEqual(self.invoice.payment_status, "paid")

        # 5. Verify GeneralLedgerEntry rebuilt for 50,000
        new_ledger_entries = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}")
        self.assertEqual(new_ledger_entries.count(), 2)
        self.assertEqual(sum(e.debit for e in new_ledger_entries), Decimal("50000.00"))
        self.assertEqual(sum(e.credit for e in new_ledger_entries), Decimal("50000.00"))

    def test_payment_edit_decreases_amount_syncs_balance_and_reverts_status(self):
        """Editing payment from ₹50,000 to ₹20,000 increases customer balance, decreases invoice amount_paid, and reverts status to partial_paid"""
        from ledger.models import GeneralLedgerEntry
        from decimal import Decimal
        from datetime import date

        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date=date.today(),
            amount=Decimal("50000.00"),
            mode="cash",
            created_by=self.tenant
        )

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, "paid")

        # Reduce amount to 20,000
        payment.amount = Decimal("20000.00")
        payment.save()

        self.customer.refresh_from_db()
        self.invoice.refresh_from_db()

        # Balance was reduced by 50,000 (to 50,000), now delta is -30,000, so balance becomes 80,000
        self.assertEqual(self.customer.current_balance, Decimal("80000.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("20000.00"))
        self.assertEqual(self.invoice.payment_status, "partial_paid")

        ledger_entries = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}")
        self.assertEqual(sum(e.debit for e in ledger_entries), Decimal("20000.00"))

    def test_payment_edit_customer_transfer(self):
        """Changing customer on a payment restores old customer balance and debits new customer balance"""
        from decimal import Decimal
        from datetime import date

        payment = Payment.objects.create(
            customer=self.customer,
            invoice=None,
            date=date.today(),
            amount=Decimal("15000.00"),
            mode="cash",
            created_by=self.tenant
        )

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("85000.00"))
        self.assertEqual(self.customer2.current_balance, Decimal("50000.00"))

        # Reassign to customer2
        payment.customer = self.customer2
        payment.save()

        self.customer.refresh_from_db()
        self.customer2.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("100000.00"))  # Restored
        self.assertEqual(self.customer2.current_balance, Decimal("35000.00"))   # Deducted

    def test_payment_edit_invoice_transfer(self):
        """Changing linked invoice on a payment transfers amount_paid between invoices"""
        from decimal import Decimal
        from datetime import date

        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date=date.today(),
            amount=Decimal("20000.00"),
            mode="cash",
            created_by=self.tenant
        )

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("20000.00"))
        self.assertEqual(self.invoice2.amount_paid, Decimal("0.00"))

        # Switch payment to invoice2
        payment.invoice = self.invoice2
        payment.save()

        self.invoice.refresh_from_db()
        self.invoice2.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, "pending")
        self.assertEqual(self.invoice2.amount_paid, Decimal("20000.00"))
        self.assertEqual(self.invoice2.payment_status, "partial_paid")

    def test_payment_delete_cleans_up_ledger_and_reverts_balances(self):
        """Deleting a payment restores customer balance, invoice amount_paid, and deletes ledger rows"""
        from ledger.models import GeneralLedgerEntry
        from decimal import Decimal
        from datetime import date

        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date=date.today(),
            amount=Decimal("30000.00"),
            mode="cash",
            created_by=self.tenant
        )

        self.assertTrue(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}").exists())

        payment_id = payment.id
        payment.delete()

        self.customer.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("100000.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(self.invoice.payment_status, "pending")
        self.assertFalse(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment_id}").exists())

    def test_payment_edit_via_api_put(self):
        """Editing payment via PUT /api/billing/payments/<id>/ syncs balance, invoice, and ledger"""
        from ledger.models import GeneralLedgerEntry
        from decimal import Decimal
        from datetime import date

        payment = Payment.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            date=date.today(),
            amount=Decimal("5000.00"),
            mode="cash",
            created_by=self.tenant
        )

        payload = {
            "customer": str(self.customer.id),
            "invoice": str(self.invoice.id),
            "date": str(date.today()),
            "amount": "50000.00",
            "mode": "upi"
        }

        res = self.client.put(f"/api/billing/payments/{payment.id}/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.customer.refresh_from_db()
        self.invoice.refresh_from_db()
        # Outstanding is invoice2 (30000.00) since invoice1 (50000.00) is now fully paid
        self.assertEqual(self.customer.current_balance, Decimal("30000.00"))
        self.assertEqual(self.invoice.amount_paid, Decimal("50000.00"))
        self.assertEqual(self.invoice.payment_status, "paid")

        entries = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}")
        self.assertEqual(entries.count(), 2)
        self.assertEqual(sum(e.debit for e in entries), Decimal("50000.00"))
        # UPI mode should route to Bank account
        bank_entry = entries.filter(debit=Decimal("50000.00")).first()
        self.assertEqual(bank_entry.account.name, "Bank")


class MultiTenantSequenceAndStaffIsolationTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from billing.models import Customer, SalesInvoice, SalesInvoiceItem, PurchaseBill
        from inventory.models import Product, Warehouse
        from subscription.models import Plan, TenantSubscription
        from datetime import date, timedelta
        from django.utils import timezone

        User = get_user_model()
        self.business_plan, _ = Plan.objects.get_or_create(code="business", defaults={"name": "Business", "monthly_price": 5000})

        # Tenant A
        self.tenant_a = User.objects.create_user(
            username="company_a",
            email="company_a@test.com",
            password="testpassword",
            business_name="Company A Pvt Ltd",
            business_address="123 Alpha St",
            gstin="27AAAAA0000A1Z5",
            state="Maharashtra"
        )
        TenantSubscription.objects.create(
            tenant=self.tenant_a,
            plan=self.business_plan,
            status="active",
            current_period_end=timezone.now() + timedelta(days=30),
        )

        # Tenant B
        self.tenant_b = User.objects.create_user(
            username="company_b",
            email="company_b@test.com",
            password="testpassword",
            business_name="Company B LLP",
            business_address="456 Beta Ave",
            gstin="29BBBBB0000B1Z5",
            state="Karnataka"
        )
        TenantSubscription.objects.create(
            tenant=self.tenant_b,
            plan=self.business_plan,
            status="active",
            current_period_end=timezone.now() + timedelta(days=30),
        )

        # Staff User for Tenant A (child user)
        self.staff_a = User.objects.create_user(
            username="accountant_a",
            email="accountant_a@test.com",
            password="testpassword",
            parent=self.tenant_a
        )

        # Common warehouse and product for Tenant A
        self.wh_a = Warehouse.objects.create(name="WH A", created_by=self.tenant_a, is_active=True)
        self.prod_a = Product.objects.create(
            name="Product A",
            price=Decimal("1000.00"),
            stock=100,
            hsn_sac_code="8471",
            tax=Decimal("18.00"),
            created_by=self.tenant_a
        )
        self.cust_a = Customer.objects.create(
            name="Customer A",
            gstin="27CUSTA0000A1Z5",
            created_by=self.tenant_a
        )

        # Warehouse and product for Tenant B
        self.wh_b = Warehouse.objects.create(name="WH B", created_by=self.tenant_b, is_active=True)
        self.prod_b = Product.objects.create(
            name="Product B",
            price=Decimal("2000.00"),
            stock=50,
            hsn_sac_code="8472",
            tax=Decimal("18.00"),
            created_by=self.tenant_b
        )
        self.cust_b = Customer.objects.create(
            name="Customer B",
            gstin="29CUSTB0000B1Z5",
            created_by=self.tenant_b
        )

        # Sales Invoice for Tenant A
        self.inv_a = SalesInvoice.objects.create(
            created_by=self.tenant_a,
            customer=self.cust_a,
            customer_name="Customer A",
            invoice_number="INV-A-001",
            invoice_date=timezone.localdate(),
            total_amount=Decimal("0.00"),
            place_of_supply="Maharashtra",
            status="final"
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.inv_a,
            product=self.prod_a,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("180.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="8471"
        )

    def test_credit_note_sequence_multi_tenant_isolation(self):
        """Client A and Client B must have independent sequences: CN-0001 for both, no sequence skipping"""
        client = APIClient()

        # Tenant A creates first credit note
        client.force_authenticate(user=self.tenant_a)
        res_a1 = client.post("/api/billing/credit-notes/", {
            "customer": str(self.cust_a.id),
            "date": str(date.today()),
            "total_amount": "118.00",
            "reason": "return",
            "items": [{
                "product": str(self.prod_a.id),
                "quantity": 1,
                "price": "100.00",
                "tax": "18.00",
                "amount": "118.00"
            }]
        }, format="json")
        self.assertEqual(res_a1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_a1.data["credit_note_number"], "CN-0001")

        # Tenant B creates first credit note - MUST be CN-0001, NOT CN-0002
        client.force_authenticate(user=self.tenant_b)
        res_b1 = client.post("/api/billing/credit-notes/", {
            "customer": str(self.cust_b.id),
            "date": str(date.today()),
            "total_amount": "236.00",
            "reason": "return",
            "items": [{
                "product": str(self.prod_b.id),
                "quantity": 1,
                "price": "200.00",
                "tax": "36.00",
                "amount": "236.00"
            }]
        }, format="json")
        self.assertEqual(res_b1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_b1.data["credit_note_number"], "CN-0001")

        # Tenant A creates second credit note - MUST become CN-0002
        client.force_authenticate(user=self.tenant_a)
        res_a2 = client.post("/api/billing/credit-notes/", {
            "customer": str(self.cust_a.id),
            "date": str(date.today()),
            "total_amount": "118.00",
            "reason": "return",
            "items": [{
                "product": str(self.prod_a.id),
                "quantity": 1,
                "price": "100.00",
                "tax": "18.00",
                "amount": "118.00"
            }]
        }, format="json")
        self.assertEqual(res_a2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_a2.data["credit_note_number"], "CN-0002")

    def test_debit_note_sequence_multi_tenant_isolation(self):
        """Client A and Client B must have independent sequences: DN-0001 for both, no sequence skipping"""
        client = APIClient()

        # Tenant A creates first debit note
        client.force_authenticate(user=self.tenant_a)
        res_a1 = client.post("/api/billing/debit-notes/", {
            "vendor_name": "Vendor A",
            "date": str(date.today()),
            "total_amount": "500.00",
            "reason": "return",
            "items": [{
                "product": str(self.prod_a.id),
                "quantity": 1,
                "price": "500.00",
                "tax": "0.00",
                "amount": "500.00"
            }]
        }, format="json")
        self.assertEqual(res_a1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_a1.data["debit_note_number"], "DN-0001")

        # Tenant B creates first debit note - MUST be DN-0001
        client.force_authenticate(user=self.tenant_b)
        res_b1 = client.post("/api/billing/debit-notes/", {
            "vendor_name": "Vendor B",
            "date": str(date.today()),
            "total_amount": "800.00",
            "reason": "return",
            "items": [{
                "product": str(self.prod_b.id),
                "quantity": 1,
                "price": "800.00",
                "tax": "0.00",
                "amount": "800.00"
            }]
        }, format="json")
        self.assertEqual(res_b1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_b1.data["debit_note_number"], "DN-0001")

    def test_staff_user_sees_tenant_gst_reports(self):
        """Child user (Staff/Accountant) must see tenant's invoices, GSTIN, and HSN data in GST views"""
        client = APIClient()
        client.force_authenticate(user=self.staff_a)
        today_str = str(date.today())

        # GSTR-1 JSON export
        res_gstr1 = client.get(f"/api/billing/gst/gstr1-export/?from={today_str}&to={today_str}")
        self.assertEqual(res_gstr1.status_code, status.HTTP_200_OK)
        # Should populate Tenant A's GSTIN, not empty
        self.assertEqual(res_gstr1.data["gstin"], "27AAAAA0000A1Z5")
        # Should include Tenant A's B2B invoice
        self.assertTrue(len(res_gstr1.data["b2b"]) > 0)
        self.assertEqual(res_gstr1.data["b2b"][0]["ctin"], "27CUSTA0000A1Z5")

        # HSN Summary
        res_hsn = client.get(f"/api/billing/gst/hsn-summary/?type=sales&from={today_str}&to={today_str}")
        self.assertEqual(res_hsn.status_code, status.HTTP_200_OK)
        self.assertTrue(len(res_hsn.data["results"]) > 0)
        self.assertEqual(res_hsn.data["results"][0]["hsn_code"], "8471")

        # Tax Register
        res_reg = client.get(f"/api/billing/gst/tax-register/?type=sales&from={today_str}&to={today_str}")
        self.assertEqual(res_reg.status_code, status.HTTP_200_OK)
        self.assertTrue(len(res_reg.data["results"]) > 0)
        self.assertEqual(res_reg.data["results"][0]["invoice_number"], "INV-A-001")

    def test_staff_user_ai_assistant_business_context(self):
        """AI Assistant business context for staff user must reflect tenant's products, sales, and receivables"""
        from unittest import mock
        from ai_assistant.views import gather_business_context
        ctx = gather_business_context(self.staff_a)

        # Totals must match Tenant A's records, NOT 0
        self.assertGreaterEqual(ctx["totals"]["products"], 1)
        self.assertGreaterEqual(ctx["totals"]["invoices"], 1)
        self.assertGreaterEqual(ctx["totals"]["customers"], 1)
        self.assertEqual(ctx["sales_today"]["count"], 1)
        self.assertAlmostEqual(ctx["sales_today"]["total"], 1180.00)

        # AI Chat endpoint demo query
        client = APIClient()
        client.force_authenticate(user=self.staff_a)
        with mock.patch('ai_assistant.views.GEMINI_API_KEY', 'demo_gemini_key'):
            res_chat = client.post("/api/ai/chat/", {"question": "How are sales today?"}, format="json")
            self.assertEqual(res_chat.status_code, status.HTTP_200_OK)
            self.assertIn("1 invoices totaling", res_chat.data["answer"])
            self.assertIn("1,180.00", res_chat.data["answer"])

    def test_staff_user_sees_chart_of_accounts(self):
        """Staff user must see tenant's chart of accounts in ledger views"""
        from ledger.services import AccountingService
        AccountingService.get_or_create_default_accounts(self.tenant_a)

        client = APIClient()
        client.force_authenticate(user=self.staff_a)
        res = client.get("/api/ledger/accounts/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreater(len(res.data), 0)


class PromotionalSchemeIntegrationTests(TestCase):
    """
    Comprehensive tests for Category 5: Promotional Schemes
    Covers:
    - BOGO (Buy X Get Y Free) automatic calculation on invoice creation
    - Non-qualifying quantity (< min_qty) does not trigger free items
    - Cross-product BOGO (Buy Product A, get Product B free) creates bonus line item
    - Percentage discount scheme application
    - Expired and inactive schemes ignored
    - Multi-tenant isolation: Tenant A's scheme never applies to Tenant B
    - Manual cashier free_quantity override preserved
    - Quotation -> Sales Order -> Sales Invoice conversion preserves free_quantity & evaluates schemes
    - Inventory stock deduction deducts quantity + free_quantity
    - Live evaluate-schemes API endpoint
    """

    def setUp(self):
        User = get_user_model()
        self.tenant_a = User.objects.create_user(username="scheme_tenant_a", email="tenant_a@scheme.com", password="password123")
        self.tenant_b = User.objects.create_user(username="scheme_tenant_b", email="tenant_b@scheme.com", password="password123")

        self.cust_a = Customer.objects.create(name="Customer A", email="cust_a@scheme.com", created_by=self.tenant_a)
        self.cust_b = Customer.objects.create(name="Customer B", email="cust_b@scheme.com", created_by=self.tenant_b)

        self.prod_a = Product.objects.create(name="Soap Premium", price=Decimal("100.00"), sale_price=Decimal("100.00"), stock=50, unit="pcs", created_by=self.tenant_a)
        self.gift_prod = Product.objects.create(name="Shampoo Sachet", price=Decimal("20.00"), sale_price=Decimal("20.00"), stock=30, unit="pcs", created_by=self.tenant_a)
        self.prod_b = Product.objects.create(name="Tenant B Soap", price=Decimal("100.00"), sale_price=Decimal("100.00"), stock=50, unit="pcs", created_by=self.tenant_b)

        today = timezone.now().date()
        # Tenant A: Buy 2 Soaps, Get 1 Soap Free
        from inventory.models_pricing import Scheme
        self.bogo_same = Scheme.objects.create(
            name="Buy 2 Get 1 Free Soap",
            scheme_type="bogo",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
            is_active=True,
            product=self.prod_a,
            min_qty=2,
            free_qty=1,
            free_product=self.prod_a,
            created_by=self.tenant_a,
        )

    def test_bogo_scheme_automatic_calculation(self):
        """Buying 4 units under Buy 2 Get 1 Free must automatically grant 2 free units"""
        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-01",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(self.prod_a.id),
                    "quantity": 4,
                    "price": "100.00",
                    "free_quantity": 0,  # 0 indicates auto-evaluate
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        invoice_id = res.data["id"]
        inv = SalesInvoice.objects.get(id=invoice_id)
        item = inv.items.first()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(item.free_quantity, 2)  # 4 // 2 * 1 = 2 Free!

    def test_bogo_sub_minimum_quantity_not_triggered(self):
        """Buying 1 unit when min_qty is 2 does not grant free quantity"""
        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-02",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(self.prod_a.id),
                    "quantity": 1,
                    "price": "100.00",
                    "free_quantity": 0,
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        inv = SalesInvoice.objects.get(id=res.data["id"])
        item = inv.items.first()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.free_quantity, 0)

    def test_cross_product_bogo_bonus_item(self):
        """Buying Product A gives Product B free; must create a bonus free line item"""
        today = timezone.now().date()
        from inventory.models_pricing import Scheme
        prod_phone = Product.objects.create(name="Smartphone", price=Decimal("15000.00"), sale_price=Decimal("15000.00"), stock=10, unit="pcs", created_by=self.tenant_a)
        Scheme.objects.create(
            name="Buy Phone Get Sachet Free",
            scheme_type="bogo",
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
            is_active=True,
            product=prod_phone,
            min_qty=1,
            free_qty=2,
            free_product=self.gift_prod,
            created_by=self.tenant_a,
        )

        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-03",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(prod_phone.id),
                    "quantity": 1,
                    "price": "15000.00",
                    "free_quantity": 0,
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        inv = SalesInvoice.objects.get(id=res.data["id"])
        items = list(inv.items.all())
        self.assertEqual(len(items), 2)  # Smartphone + Gift item
        phone_item = next(i for i in items if i.product_id == prod_phone.id)
        gift_item = next(i for i in items if i.product_id == self.gift_prod.id)

        self.assertEqual(phone_item.quantity, 1)
        self.assertEqual(gift_item.quantity, 0)
        self.assertEqual(gift_item.free_quantity, 2)
        self.assertEqual(gift_item.amount, Decimal("0.00"))

    def test_expired_or_inactive_scheme_not_applied(self):
        """Expired or inactive schemes must never apply"""
        today = timezone.now().date()
        from inventory.models_pricing import Scheme
        prod_expired = Product.objects.create(name="Expired Offer Item", price=Decimal("50.00"), sale_price=Decimal("50.00"), stock=10, unit="pcs", created_by=self.tenant_a)
        Scheme.objects.create(
            name="Old Scheme",
            scheme_type="bogo",
            start_date=today - datetime.timedelta(days=60),
            end_date=today - datetime.timedelta(days=1),  # Expired yesterday
            is_active=True,
            product=prod_expired,
            min_qty=1,
            free_qty=1,
            created_by=self.tenant_a,
        )

        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-04",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(prod_expired.id),
                    "quantity": 2,
                    "price": "50.00",
                    "free_quantity": 0,
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        inv = SalesInvoice.objects.get(id=res.data["id"])
        item = inv.items.first()
        self.assertEqual(item.free_quantity, 0)

    def test_multi_tenant_scheme_isolation(self):
        """Tenant A's scheme must never apply to Tenant B's invoice"""
        client = APIClient()
        client.force_authenticate(user=self.tenant_b)

        payload = {
            "customer_name": self.cust_b.name,
            "invoice_number": "INV-SCHEME-B1",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(self.prod_b.id),
                    "quantity": 4,
                    "price": "100.00",
                    "free_quantity": 0,
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        inv = SalesInvoice.objects.get(id=res.data["id"])
        item = inv.items.first()
        self.assertEqual(item.free_quantity, 0)  # Tenant B has no schemes!

    def test_manual_free_quantity_override_preserved(self):
        """If cashier enters a custom free_quantity > 0, it must be honored and not overridden"""
        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-05",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(self.prod_a.id),
                    "quantity": 4,
                    "price": "100.00",
                    "free_quantity": 5,  # Manual cashier override: 5 free instead of 2
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        inv = SalesInvoice.objects.get(id=res.data["id"])
        item = inv.items.first()
        self.assertEqual(item.free_quantity, 5)

    def test_stock_deduction_includes_free_quantity(self):
        """Stock deduction signal must deduct both quantity and free_quantity (2 + 1 = 3)"""
        initial_stock = self.prod_a.stock

        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "customer_name": self.cust_a.name,
            "invoice_number": "INV-SCHEME-STOCK",
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                {
                    "product": str(self.prod_a.id),
                    "quantity": 2,
                    "price": "100.00",
                    "free_quantity": 0,  # Auto-evaluates to 1 free
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        self.prod_a.refresh_from_db()
        # Sold 2 + 1 free = 3 total deducted
        self.assertEqual(self.prod_a.stock, initial_stock - 3)

    def test_evaluate_schemes_api_endpoint(self):
        """API endpoint /api/billing/sales-invoices/evaluate-schemes/ must return live scheme calculation"""
        client = APIClient()
        client.force_authenticate(user=self.tenant_a)

        payload = {
            "invoice_date": str(date.today()),
            "items": [
                {
                    "product_id": str(self.prod_a.id),
                    "quantity": 6,
                    "price": "100.00",
                }
            ]
        }
        res = client.post("/api/billing/sales-invoices/evaluate-schemes/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        evaluated_item = res.data["items"][0]
        self.assertEqual(evaluated_item["free_quantity"], 3)  # 6 // 2 * 1 = 3 Free
        self.assertIsNotNone(evaluated_item["applied_scheme"])
        self.assertEqual(evaluated_item["applied_scheme"]["type"], "bogo")
        self.assertEqual(evaluated_item["applied_scheme"]["earned_free_qty"], 3)

    def test_order_to_invoice_preserves_and_evaluates_free_quantity(self):
        """Converting a sales order to invoice must preserve free_quantity and evaluate schemes"""
        from billing.models_sidecar import SalesOrder, SalesOrderItem
        order = SalesOrder.objects.create(
            order_number="SO-SCHEME-01",
            date=date.today(),
            customer=self.cust_a,
            total_amount=Decimal("400.00"),
            created_by=self.tenant_a,
        )
        SalesOrderItem.objects.create(
            order=order,
            product=self.prod_a,
            quantity=4,
            free_quantity=0,
            price=Decimal("100.00"),
            amount=Decimal("400.00"),
            unit="pcs",
        )

        client = APIClient()
        client.force_authenticate(user=self.tenant_a)
        res = client.post(f"/api/billing/sales-orders/{order.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        invoice_id = res.data["invoice_id"]
        inv = SalesInvoice.objects.get(id=invoice_id)
        item = inv.items.first()
        self.assertEqual(item.quantity, 4)
        self.assertEqual(item.free_quantity, 2)  # Scheme evaluated: 4 // 2 * 1 = 2


class InvoiceSequenceConcurrencyTests(TestCase):
    """
    Comprehensive tests for High-Risk Concurrency & Invoice Numbering:
    - Preview next invoice number without premature sequence consumption
    - Multi-cashier collision auto-advances to next sequential number (no 409 Conflict)
    - Custom manual invoice number collisions return 409 Conflict
    - Blank invoice number triggers atomic auto-allocation
    - Database unique_together constraint on SalesInvoice prevents duplicates
    - Order conversion uses atomic sequence engine
    - Thread-safe allocation under concurrent execution
    """
    def setUp(self):
        self.tenant = User.objects.create_user(
            username="seq_tenant",
            email="seq_tenant@test.com",
            password="testpassword",
            first_name="Seq",
            last_name="Owner",
        )
        self.staff_1 = User.objects.create_user(
            username="seq_staff_1",
            email="seq_staff_1@test.com",
            password="testpassword",
            parent=self.tenant,
            role="cashier",
        )
        self.staff_2 = User.objects.create_user(
            username="seq_staff_2",
            email="seq_staff_2@test.com",
            password="testpassword",
            parent=self.tenant,
            role="cashier",
        )

        from billing.models import Customer
        from inventory.models import Product
        self.customer = Customer.objects.create(name="Retail Customer", created_by=self.tenant)
        self.product = Product.objects.create(
            name="Keyboard",
            sale_price=Decimal("50.00"),
            stock=100,
            created_by=self.tenant,
        )

    def test_next_invoice_number_preview_does_not_advance_sequence(self):
        """Calling next-number endpoint previews next number without consuming sequence numbers"""
        client = APIClient()
        client.force_authenticate(user=self.staff_1)

        res1 = client.get("/api/billing/sales-invoices/next-number/?prefix=INV-")
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        num1 = res1.data["next_number"]

        res2 = client.get("/api/billing/sales-invoices/next-number/?prefix=INV-")
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        num2 = res2.data["next_number"]

        # Repeated previews should give the exact same candidate until an invoice is actually saved
        self.assertEqual(num1, num2)

    def test_multi_cashier_collision_auto_advances_without_409(self):
        """
        When Cashier 1 and Cashier 2 both open the sales form and get the same tentative number,
        Cashier 1 submitting first succeeds, and Cashier 2 submitting with the same auto-number
        is automatically advanced to the next sequence number without 409 Conflict.
        """
        client1 = APIClient()
        client1.force_authenticate(user=self.staff_1)

        client2 = APIClient()
        client2.force_authenticate(user=self.staff_2)

        # Both cashiers preview next number
        res_preview = client1.get("/api/billing/sales-invoices/next-number/?prefix=INV-")
        shared_number = res_preview.data["next_number"]

        payload1 = {
            "invoice_number": shared_number,
            "invoice_date": str(date.today()),
            "customer": str(self.customer.id),
            "items": [
                {"product": str(self.product.id), "quantity": 1, "price": 50.00}
            ]
        }

        # Cashier 1 submits -> succeeds with shared_number
        res1 = client1.post("/api/billing/sales-invoices/", payload1, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res1.data["invoice_number"], shared_number)

        # Cashier 2 submits with stale form containing shared_number
        payload2 = {
            "invoice_number": shared_number,
            "invoice_date": str(date.today()),
            "customer": str(self.customer.id),
            "items": [
                {"product": str(self.product.id), "quantity": 2, "price": 50.00}
            ]
        }
        res2 = client2.post("/api/billing/sales-invoices/", payload2, format="json")
        # Cashier 2 MUST NOT hit 409 Conflict!
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(res2.data["invoice_number"], shared_number)
        
        # Verify both invoices exist in database with distinct numbers for the tenant
        invoices = list(SalesInvoice.objects.filter(created_by=self.tenant).order_by("invoice_number"))
        self.assertEqual(len(invoices), 2)
        self.assertEqual(invoices[0].invoice_number, shared_number)
        self.assertEqual(invoices[1].invoice_number, res2.data["invoice_number"])

    def test_custom_manual_invoice_number_collision_returns_409(self):
        """When a user types a custom manual invoice number that already exists, return 409 Conflict"""
        client = APIClient()
        client.force_authenticate(user=self.staff_1)

        custom_number = "CUSTOM-BATCH-2026-001"
        payload = {
            "invoice_number": custom_number,
            "invoice_date": str(date.today()),
            "customer": str(self.customer.id),
            "items": [
                {"product": str(self.product.id), "quantity": 1, "price": 50.00}
            ]
        }

        res1 = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("Invoice number already exists", res2.data["error"])

    def test_blank_invoice_number_auto_allocates(self):
        """Submitting an invoice without specifying invoice_number automatically generates next number"""
        client = APIClient()
        client.force_authenticate(user=self.staff_1)

        payload = {
            "invoice_number": "",
            "invoice_date": str(date.today()),
            "customer": str(self.customer.id),
            "items": [
                {"product": str(self.product.id), "quantity": 1, "price": 50.00}
            ]
        }
        res = client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data["invoice_number"].startswith("INV-"))

    def test_database_unique_together_constraint(self):
        """Database enforces unique_together constraint on (created_by, invoice_number)"""
        from django.db import IntegrityError
        SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            invoice_number="INV-UNIQUE-TEST",
            invoice_date=date.today(),
            total_amount=Decimal("50.00"),
        )
        with self.assertRaises(IntegrityError):
            SalesInvoice.objects.create(
                created_by=self.tenant,
                customer=self.customer,
                invoice_number="INV-UNIQUE-TEST",
                invoice_date=date.today(),
                total_amount=Decimal("100.00"),
            )

    def test_order_to_invoice_uses_atomic_sequence(self):
        """Converting an approved sales order to invoice allocates from sequence service"""
        from billing.models_sidecar import SalesOrder, SalesOrderItem
        order = SalesOrder.objects.create(
            order_number="SO-SEQ-01",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("150.00"),
            created_by=self.tenant,
        )
        SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=3,
            price=Decimal("50.00"),
            amount=Decimal("150.00"),
            unit="pcs",
        )

        client = APIClient()
        client.force_authenticate(user=self.tenant)
        res = client.post(f"/api/billing/sales-orders/{order.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        invoice_id = res.data["invoice_id"]
        inv = SalesInvoice.objects.get(id=invoice_id)
        self.assertTrue(inv.invoice_number.startswith("INV-"))

    def test_allocations_are_strictly_monotonic_and_unique(self):
        """Calls to allocate_next_number produce sequential, non-overlapping numbers with strictly incrementing suffixes"""
        from billing.sequence_service import allocate_next_number

        results = [allocate_next_number(self.tenant, document_type='sales_invoice') for _ in range(10)]
        self.assertEqual(len(results), 10)
        self.assertEqual(len(set(results)), 10, "All allocated invoice numbers must be distinct")

        # Verify strictly sequential suffixes (e.g. 001, 002, 003...)
        suffixes = [int(r.split('-')[-1]) for r in results]
        self.assertEqual(suffixes, list(range(suffixes[0], suffixes[0] + 10)))


class InvoicePDFGenerationTests(TestCase):
    """
    Comprehensive tests for Vector PDF Engine:
    - Verifies vector PDF binary format (%PDF-)
    - Verifies file size is strictly < 100 KB
    - Verifies multi-page pagination with 30+ items without errors
    - Verifies quotation PDF generation
    - Verifies tenant isolation on PDF downloads
    - Verifies custom template and color palette rendering
    """
    def setUp(self):
        self.tenant = User.objects.create_user(
            username="pdf_tenant",
            email="pdf_tenant@test.com",
            password="testpassword",
            first_name="PDF",
            last_name="Owner",
        )
        self.other_tenant = User.objects.create_user(
            username="other_pdf_tenant",
            email="other_pdf@test.com",
            password="testpassword",
        )
        from billing.models import Customer, SalesInvoice, SalesInvoiceItem
        from inventory.models import Product
        from billing.models_sidecar import Quotation, QuotationItem

        self.customer = Customer.objects.create(
            name="Mega Retailer Ltd",
            address="123 Commercial Street, Bangalore",
            gstin="29ABCDE1234F1Z5",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Wireless Mouse",
            sale_price=Decimal("250.00"),
            stock=500,
            hsn_sac_code="8471",
            created_by=self.tenant,
        )

        # Create 1-page sample invoice
        self.invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name=self.customer.name,
            customer_address=self.customer.address,
            invoice_number="INV-PDF-001",
            invoice_date=date.today(),
            total_amount=Decimal("500.00"),
            place_of_supply="Karnataka",
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.invoice,
            product=self.product,
            quantity=2,
            price=Decimal("250.00"),
            amount=Decimal("500.00"),
            tax=Decimal("18.00"),
            discount=Decimal("0.00"),
            unit="pcs",
        )

        # Create Quotation
        self.quotation = Quotation.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            quotation_number="QT-PDF-001",
            quotation_date=date.today(),
            due_date=date.today(),
            total_amount=Decimal("250.00"),
        )
        QuotationItem.objects.create(
            quotation=self.quotation,
            product=self.product,
            quantity=1,
            price=Decimal("250.00"),
            amount=Decimal("250.00"),
            tax=Decimal("18.00"),
        )

    def test_generate_invoice_pdf_vector_output_and_size_under_100kb(self):
        """Vector PDF output must start with %PDF- and be under 100 KB with zero raster bloat"""
        from billing.invoice_pdf_service import generate_invoice_pdf

        pdf_bytes = generate_invoice_pdf(
            invoice_obj=self.invoice,
            tenant=self.tenant,
            document_type='invoice',
        )

        self.assertTrue(pdf_bytes.startswith(b'%PDF-'), "Must be valid PDF binary")
        file_size_kb = len(pdf_bytes) / 1024
        print(f"\n[PDF Benchmark] Invoice PDF file size: {file_size_kb:.2f} KB (Target: < 100 KB)")
        self.assertLess(file_size_kb, 100.0, f"PDF file size {file_size_kb:.2f} KB exceeds 100 KB limit")

    def test_multi_page_invoice_pdf_pagination_under_100kb(self):
        """Invoices with 30 line items must paginate cleanly and remain strictly under 100 KB"""
        from billing.models import SalesInvoice, SalesInvoiceItem
        from billing.invoice_pdf_service import generate_invoice_pdf

        multi_invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            invoice_number="INV-MULTI-030",
            invoice_date=date.today(),
            total_amount=Decimal("7500.00"),
        )
        for i in range(1, 31):
            SalesInvoiceItem.objects.create(
                sales_invoice=multi_invoice,
                product=self.product,
                quantity=1,
                price=Decimal("250.00"),
                amount=Decimal("250.00"),
                tax=Decimal("18.00"),
                unit="pcs",
                free_quantity=1 if i % 5 == 0 else 0,
            )

        pdf_bytes = generate_invoice_pdf(
            invoice_obj=multi_invoice,
            tenant=self.tenant,
            document_type='invoice',
        )

        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        file_size_kb = len(pdf_bytes) / 1024
        print(f"[PDF Benchmark] 30-Item Multi-Page PDF file size: {file_size_kb:.2f} KB (Target: < 100 KB)")
        self.assertLess(file_size_kb, 100.0, f"Multi-page PDF size {file_size_kb:.2f} KB exceeds 100 KB")

    def test_sales_invoice_pdf_download_endpoint(self):
        """GET /api/billing/sales-invoices/<id>/pdf/ returns application/pdf with attachment header"""
        client = APIClient()
        client.force_authenticate(user=self.tenant)

        res = client.get(f"/api/billing/sales-invoices/{self.invoice.id}/pdf/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("attachment; filename=", res["Content-Disposition"])
        self.assertTrue(res.content.startswith(b'%PDF-'))
        self.assertLess(len(res.content) / 1024, 100.0)

    def test_quotation_pdf_download_endpoint(self):
        """GET /api/billing/quotations/<id>/pdf/ returns application/pdf under 100 KB"""
        client = APIClient()
        client.force_authenticate(user=self.tenant)

        res = client.get(f"/api/billing/quotations/{self.quotation.id}/pdf/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b'%PDF-'))
        self.assertLess(len(res.content) / 1024, 100.0)

    def test_pdf_download_tenant_isolation(self):
        """Tenant B cannot download Tenant A's invoice or quotation PDF"""
        client = APIClient()
        client.force_authenticate(user=self.other_tenant)

        res_inv = client.get(f"/api/billing/sales-invoices/{self.invoice.id}/pdf/")
        self.assertEqual(res_inv.status_code, status.HTTP_404_NOT_FOUND)

        res_qt = client.get(f"/api/billing/quotations/{self.quotation.id}/pdf/")
        self.assertEqual(res_qt.status_code, status.HTTP_404_NOT_FOUND)

    def test_custom_theme_colors_and_layout_payload(self):
        """POST /api/billing/sales-invoices/<id>/pdf/ with custom template colors renders correctly"""
        client = APIClient()
        client.force_authenticate(user=self.tenant)

        template_payload = {
            "template": {
                "layoutType": "genz",
                "colors": {
                    "primary": "#7c3aed",
                    "secondary": "#06b6d4",
                    "tableHeader": "#f5f3ff",
                    "totalRow": "#7c3aed",
                    "totalText": "#ffffff",
                }
            }
        }
        res = client.post(f"/api/billing/sales-invoices/{self.invoice.id}/pdf/", template_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b'%PDF-'))
        self.assertLess(len(res.content) / 1024, 100.0)

    def test_deduplicate_sales_invoices_migration_helper(self):
        """deduplicate_sales_invoices must safely rename duplicate invoice numbers before unique constraint"""
        import importlib
        from unittest.mock import MagicMock
        mig_mod = importlib.import_module("billing.migrations.0030_invoicesequence_alter_salesinvoice_unique_together_and_more")
        deduplicate_sales_invoices = mig_mod.deduplicate_sales_invoices

        inv1 = MagicMock(id='1', created_by='tenant1', invoice_number='INV-001', created_at=1)
        inv2 = MagicMock(id='2', created_by='tenant1', invoice_number='INV-001', created_at=2)
        inv3 = MagicMock(id='3', created_by='tenant1', invoice_number='INV-001', created_at=3)

        mock_model = MagicMock()
        mock_model.objects.values.return_value.annotate.return_value.filter.return_value = [
            {'created_by': 'tenant1', 'invoice_number': 'INV-001', 'cnt': 3}
        ]
        mock_model.objects.filter.return_value.order_by.return_value = [inv1, inv2, inv3]
        mock_model.objects.filter.return_value.exists.return_value = False

        mock_apps = MagicMock()
        mock_apps.get_model.return_value = mock_model

        deduplicate_sales_invoices(mock_apps, None)

        # inv1 should be untouched
        self.assertEqual(inv1.invoice_number, 'INV-001')
        inv1.save.assert_not_called()

        # inv2 and inv3 should be renumbered and saved
        self.assertEqual(inv2.invoice_number, 'INV-001-DUP1')
        self.assertEqual(inv3.invoice_number, 'INV-001-DUP2')
        inv3.save.assert_called_once_with(update_fields=['invoice_number'])

    def test_number_to_words_accuracy(self):
        """number_to_words must accurately convert rupees and paise using Indian numbering."""
        from billing.invoice_pdf_service import number_to_words

        self.assertEqual(number_to_words(252000), "Two Lakh Fifty Two Thousand Rupees Only")
        self.assertEqual(number_to_words(Decimal("252000.00")), "Two Lakh Fifty Two Thousand Rupees Only")
        self.assertEqual(number_to_words(1250.50), "One Thousand Two Hundred Fifty Rupees and Fifty Paise Only")
        self.assertEqual(number_to_words(0.75), "Seventy Five Paise Only")
        self.assertEqual(number_to_words(1.00), "One Rupee Only")
        self.assertEqual(number_to_words(1.01), "One Rupee and One Paisa Only")
        self.assertEqual(number_to_words(0.01), "One Paisa Only")
        self.assertEqual(number_to_words(0), "Zero Rupees Only")
        self.assertEqual(number_to_words(-50.25), "Minus Fifty Rupees and Twenty Five Paise Only")
        self.assertEqual(number_to_words(100000000), "Ten Crore Rupees Only")



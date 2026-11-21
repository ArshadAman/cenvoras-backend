import importlib
from datetime import date
from decimal import Decimal
from django.apps import apps
from rest_framework import status
from billing.models import Customer, SalesInvoice
from billing.models_sidecar import SalesOrder, PartyMeta
from billing.tests_four_fixes import FourFixesBase

backfill_migration = importlib.import_module("billing.migrations.0067_backfill_document_customers")
backfill_customers = backfill_migration.backfill_customers


class CustomerGlobalSyncTests(FourFixesBase):
    def test_sales_order_auto_creates_customer_globally(self):
        """Saving a sales order with a brand new customer automatically creates the customer globally."""
        new_name = "Om Scientific and Chemicals"
        payload = {
            "order_number": "SO-TEST-001",
            "date": str(date.today()),
            "customer_name": new_name,
            "customer_gstin": "21AABCO1234F1Z5",
            "customer_address": "Plot 10, Chandaka Industrial Estate, Bhubaneswar",
            "total_amount": "9676.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 5,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "5900.00",
                    "unit": "pcs",
                }
            ],
        }

        self.assertFalse(Customer.objects.filter(name=new_name, created_by=self.tenant).exists())

        res = self.client.post("/api/billing/sales-orders/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        # 1. Customer must exist globally in the Customer database
        customer = Customer.objects.filter(name=new_name, created_by=self.tenant).first()
        self.assertIsNotNone(customer)
        self.assertEqual(customer.gstin, "21AABCO1234F1Z5")
        self.assertEqual(customer.address, "Plot 10, Chandaka Industrial Estate, Bhubaneswar")

        # 2. PartyMeta must be created
        self.assertTrue(PartyMeta.objects.filter(customer=customer).exists())

        # 3. Sales order must have customer foreign key linked
        order = SalesOrder.objects.get(id=res.data["id"])
        self.assertEqual(order.customer_id, customer.id)

    def test_sales_order_matches_existing_customer_by_gstin(self):
        """If a customer with matching GSTIN already exists, link to it and do NOT create a duplicate."""
        existing = Customer.objects.create(
            name="Centurion University",
            gstin="21AAACC1111F1Z1",
            address="Paralakhemundi, Odisha",
            created_by=self.tenant,
        )
        before_count = Customer.objects.filter(created_by=self.tenant).count()

        payload = {
            "order_number": "SO-TEST-002",
            "date": str(date.today()),
            "customer_name": "CENTURION SCHOOL OF RURAL ENTERPRISE MANAGEMENT",  # Different trade name
            "customer_gstin": "21AAACC1111F1Z1",  # Same GSTIN
            "total_amount": "3497.52",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 2,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "2360.00",
                    "unit": "pcs",
                }
            ],
        }

        res = self.client.post("/api/billing/sales-orders/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        # Count must remain the same (no duplicate created!)
        after_count = Customer.objects.filter(created_by=self.tenant).count()
        self.assertEqual(after_count, before_count)

        # Order must be linked to the existing customer
        order = SalesOrder.objects.get(id=res.data["id"])
        self.assertEqual(order.customer_id, existing.id)

    def test_sales_order_matches_existing_customer_by_name(self):
        """Case-insensitive name match links to existing customer without duplicates."""
        before_count = Customer.objects.filter(created_by=self.tenant).count()

        payload = {
            "order_number": "SO-TEST-003",
            "date": str(date.today()),
            "customer_name": "ka buyer",  # self.customer is "KA Buyer"
            "total_amount": "1180.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 1,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "1180.00",
                    "unit": "pcs",
                }
            ],
        }

        res = self.client.post("/api/billing/sales-orders/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        # Count must not increase
        self.assertEqual(Customer.objects.filter(created_by=self.tenant).count(), before_count)
        order = SalesOrder.objects.get(id=res.data["id"])
        self.assertEqual(order.customer_id, self.customer.id)

    def test_sales_invoice_auto_creates_customer_globally(self):
        """Saving a sales invoice with a new customer creates it globally."""
        new_name = "Global Auto Buyer Ltd"
        valid_gstin = "29AAGCB7383J1Z4"
        payload = {
            "invoice_number": "INV-TEST-001",
            "invoice_date": str(date.today()),
            "customer_name": new_name,
            "customer_address": "123 High Street, Bengaluru",
            "customer_gstin": valid_gstin,
            "status": "final",
            "total_amount": "1180.00",
            "items": [
                {
                    "row_type": "item",
                    "product": str(self.product.id),
                    "product_name": self.product.name,
                    "quantity": 1,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "1000.00",
                    "hsn_sac_code": "847130",
                    "unit": "pcs",
                }
            ],
        }

        res = self.client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        customer = Customer.objects.filter(name=new_name, created_by=self.tenant).first()
        self.assertIsNotNone(customer)
        self.assertEqual(customer.gstin, valid_gstin)
        self.assertTrue(PartyMeta.objects.filter(customer=customer).exists())

    def test_backfill_migration_links_and_creates_customers(self):
        """The data migration finds documents without customer FK and creates/links them."""
        # Create unlinked sales order
        so = SalesOrder.objects.create(
            order_number="SO-UNLINKED-001",
            date=date.today(),
            customer=None,
            customer_name="Unlinked Scientific Co",
            customer_gstin="21AAAFU1234A1Z9",
            customer_address="Cuttack, Odisha",
            total_amount=Decimal("5000.00"),
            created_by=self.tenant,
        )

        self.assertFalse(Customer.objects.filter(name="Unlinked Scientific Co").exists())

        # Run backfill
        backfill_customers(apps, None)

        # Assert customer is now created globally
        created_cust = Customer.objects.filter(name="Unlinked Scientific Co", created_by=self.tenant).first()
        self.assertIsNotNone(created_cust)
        self.assertEqual(created_cust.gstin, "21AAAFU1234A1Z9")

        # Assert SalesOrder now links to the customer
        so.refresh_from_db()
        self.assertEqual(so.customer_id, created_cust.id)

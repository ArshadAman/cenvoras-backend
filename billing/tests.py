from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import SalesInvoice, PurchaseBill
from datetime import date

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
        self.user = User.objects.create_user(
            username="test_user",
            email="test@test.com", 
            password="testpassword",
            first_name="Test",
            last_name="User",
            parent=self.tenant
        )
        self.client.force_authenticate(user=self.user)
        
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

    def test_analytics_endpoint(self):
        SalesInvoice.objects.create(created_by=self.tenant, customer_name="Jane Doe", invoice_number="INV-1", invoice_date="2024-01-01", total_amount=100.00)
        SalesInvoice.objects.create(created_by=self.tenant, customer_name="Jane Doe", invoice_number="INV-2", invoice_date="2024-05-05", total_amount=200.00)
        
        res = self.client.get("/api/billing/sales-invoices/analytics/?start_date=2024-01-01&end_date=2024-02-01")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_revenue'], 100.00)
        self.assertEqual(res.data['total_invoices'], 1)

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

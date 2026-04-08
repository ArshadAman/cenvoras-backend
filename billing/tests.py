from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import SalesInvoice
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

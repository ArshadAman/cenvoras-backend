from datetime import date
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from billing.models import Customer, SalesInvoice
from billing.models_sidecar import SalesOrder, Quotation
from billing.serializers import SalesInvoiceSerializer

User = get_user_model()

class CustomerDeleteAndFilterTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='testowner@example.com',
            username='testowner',
            password='testpassword123',
            business_name='Owner Corp'
        )
        self.client.force_authenticate(user=self.user)

    def test_delete_customer_no_documents_succeeds(self):
        cust = Customer.objects.create(name='Standalone Customer', created_by=self.user)
        res = self.client.delete(f'/api/billing/customers/{cust.id}/edit/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Customer.objects.filter(id=cust.id).exists())

    def test_delete_customer_with_sales_order_reports_blocking_doc(self):
        cust = Customer.objects.create(name='Customer With Order', created_by=self.user)
        so = SalesOrder.objects.create(
            order_number='SO-TEST-001',
            date=date.today(),
            customer=cust,
            customer_name=cust.name,
            total_amount=1500,
            stage='new',
            created_by=self.user
        )

        res = self.client.delete(f'/api/billing/customers/{cust.id}/edit/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data['success'])
        self.assertIn("SO-TEST-001", res.data['message'])
        self.assertEqual(len(res.data['blocking_documents']), 1)
        doc = res.data['blocking_documents'][0]
        self.assertEqual(doc['identifier'], 'SO-TEST-001')
        self.assertEqual(doc['type'], 'Sales Order')
        self.assertEqual(doc['status'], 'new')
        self.assertEqual(doc['route'], '/sales-orders')

    def test_delete_customer_with_quotation_reports_blocking_doc(self):
        cust = Customer.objects.create(name='Customer With Quote', created_by=self.user)
        q = Quotation.objects.create(
            quotation_number='QT-TEST-001',
            quotation_date=date.today(),
            customer=cust,
            customer_name=cust.name,
            status='draft',
            created_by=self.user
        )

        res = self.client.delete(f'/api/billing/customers/{cust.id}/edit/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data['success'])
        self.assertIn("QT-TEST-001", res.data['message'])
        self.assertEqual(len(res.data['blocking_documents']), 1)
        doc = res.data['blocking_documents'][0]
        self.assertEqual(doc['identifier'], 'QT-TEST-001')
        self.assertEqual(doc['type'], 'Quotation')
        self.assertEqual(doc['status'], 'draft')
        self.assertEqual(doc['route'], '/quotations')

    def test_filter_sales_orders_and_quotations_by_customer(self):
        cust1 = Customer.objects.create(name='Alpha Corp', created_by=self.user)
        cust2 = Customer.objects.create(name='Beta LLC', created_by=self.user)

        SalesOrder.objects.create(order_number='SO-A', date=date.today(), customer=cust1, total_amount=100, created_by=self.user)
        SalesOrder.objects.create(order_number='SO-B', date=date.today(), customer=cust2, total_amount=200, created_by=self.user)

        Quotation.objects.create(quotation_number='QT-A', quotation_date=date.today(), customer=cust1, created_by=self.user)
        Quotation.objects.create(quotation_number='QT-B', quotation_date=date.today(), customer=cust2, created_by=self.user)

        res_so = self.client.get(f'/api/billing/sales-orders/?customer={cust1.id}')
        self.assertEqual(res_so.status_code, status.HTTP_200_OK)
        so_data = res_so.data if isinstance(res_so.data, list) else res_so.data.get('results', [])
        self.assertEqual(len(so_data), 1)
        self.assertEqual(so_data[0]['order_number'], 'SO-A')

        res_q = self.client.get(f'/api/billing/quotations/?customer={cust1.id}')
        self.assertEqual(res_q.status_code, status.HTTP_200_OK)
        q_data = res_q.data if isinstance(res_q.data, list) else res_q.data.get('results', [])
        self.assertEqual(len(q_data), 1)
        self.assertEqual(q_data[0]['quotation_number'], 'QT-A')

    def test_delete_customer_with_sales_invoice_reports_blocking_doc(self):
        cust = Customer.objects.create(name='Customer With Invoice', created_by=self.user)
        inv = SalesInvoice.objects.create(
            invoice_number='INV-TEST-999',
            invoice_date=date.today(),
            customer=cust,
            customer_name=cust.name,
            total_amount=5000,
            status='final',
            created_by=self.user
        )

        res = self.client.delete(f'/api/billing/customers/{cust.id}/edit/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res.data['success'])
        self.assertIn("INV-TEST-999", res.data['message'])
        self.assertEqual(len(res.data['blocking_documents']), 1)
        doc = res.data['blocking_documents'][0]
        self.assertEqual(doc['identifier'], 'INV-TEST-999')
        self.assertEqual(doc['type'], 'Sales Invoice')
        self.assertEqual(doc['status'], 'final')
        self.assertEqual(doc['route'], '/sales')

    def test_sales_invoice_serializer_exposes_customer_id(self):
        cust = Customer.objects.create(name='Serialized Cust', created_by=self.user)
        inv = SalesInvoice.objects.create(
            invoice_number='INV-SER-001',
            invoice_date=date.today(),
            customer=cust,
            customer_name=cust.name,
            total_amount=1000,
            status='final',
            created_by=self.user
        )
        serialized = SalesInvoiceSerializer(inv).data
        self.assertEqual(serialized['customer'], str(cust.id))
        self.assertEqual(serialized['customer_id'], str(cust.id))
        self.assertEqual(serialized['customer_details']['id'], str(cust.id))


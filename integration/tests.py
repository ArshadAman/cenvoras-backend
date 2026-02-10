from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from integration.models import ApiKey
from inventory.models import Product
from billing.models import Customer, SalesInvoice, Payment

User = get_user_model()

class IntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = APIClient()
        
        # Create API Key
        self.api_key = ApiKey.objects.create(user=self.user, name="Test Key")
        self.client.credentials(HTTP_X_API_KEY=self.api_key.key)
        
        # Create Product
        self.product = Product.objects.create(
            name="Test Product",
            sale_price=100.00,
            stock=10,
            created_by=self.user
        )

    def test_product_list(self):
        response = self.client.get('/api/integration/products/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Test Product")

    def test_create_order_new_customer(self):
        payload = {
            "customer": {
                "name": "New Customer",
                "phone": "9876543210",
                "email": "new@example.com"
            },
            "items": [
                {"product_id": str(self.product.id), "quantity": 2}
            ],
            "payment_status": "paid",
            "payment_mode": "upi"
        }
        
        response = self.client.post('/api/integration/orders/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify Invoice
        invoice = SalesInvoice.objects.get(id=response.data['invoice_id'])
        self.assertEqual(invoice.total_amount, 200.00)
        
        # Verify Customer Created
        customer = Customer.objects.get(phone="9876543210")
        self.assertEqual(customer.name, "New Customer")
        
        # Verify Payment Recorded (balance should be 0)
        self.assertEqual(customer.current_balance, 0)
        self.assertTrue(Payment.objects.filter(reference__startswith="INV-").exists())

    def test_create_order_existing_customer(self):
        customer = Customer.objects.create(
            name="Existing Customer",
            phone="1234567890",
            created_by=self.user
        )
        
        payload = {
            "customer": {
                "phone": "1234567890"
            },
            "items": [
                {"product_id": str(self.product.id), "quantity": 1}
            ],
            "payment_status": "unpaid"
        }
        
        response = self.client.post('/api/integration/orders/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify Invoice linked to existing customer
        invoice = SalesInvoice.objects.get(id=response.data['invoice_id'])
        self.assertEqual(invoice.customer, customer)
        
        # Verify Balance (should be 100 since unpaid)
        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, 100.00)

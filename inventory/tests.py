from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from inventory.models import Product, Warehouse, ProductBatch, StockPoint
from datetime import date

User = get_user_model()


class BatchSplitValidationTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.tenant = User.objects.create_user(
			username="tenant_inv",
			email="tenant.inv@test.com",
			password="testpassword",
			first_name="Tenant",
			last_name="Inv",
		)
		self.user = User.objects.create_user(
			username="user_inv",
			email="user.inv@test.com",
			password="testpassword",
			first_name="User",
			last_name="Inv",
			parent=self.tenant,
		)
		self.client.force_authenticate(user=self.user)

		self.product = Product.objects.create(
			name="Split Product",
			price=100,
			sale_price=120,
			stock=100,
			created_by=self.tenant,
		)
		self.warehouse = Warehouse.objects.create(
			name="Main Warehouse",
			created_by=self.tenant,
		)
		self.batch = ProductBatch.objects.create(
			product=self.product,
			batch_number="BATCH-001",
			expiry_date=date.today(),
			cost_price=100,
			sale_price=120,
			mrp=130,
		)
		StockPoint.objects.create(
			batch=self.batch,
			warehouse=self.warehouse,
			quantity=80,
		)

	def test_batch_split_non_integer_quantity_returns_400(self):
		payload = {
			"batch_id": str(self.batch.id),
			"new_batch_number": "BATCH-001-S1",
			"split_quantity": "abc",
		}
		res = self.client.post("/api/inventory/batches/split/", payload, format='json')
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(res.data.get('error'), 'split_quantity must be a valid integer.')

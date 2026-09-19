from django.test import TestCase
from django.core.cache import cache
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


class ProductCreateIdempotencyTests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()
		self.tenant = User.objects.create_user(
			username="tenant_products",
			email="tenant.products@test.com",
			password="testpassword",
			first_name="Tenant",
			last_name="Products",
		)
		self.user = User.objects.create_user(
			username="user_products",
			email="user.products@test.com",
			password="testpassword",
			first_name="User",
			last_name="Products",
			parent=self.tenant,
		)
		self.client.force_authenticate(user=self.user)

	def test_duplicate_product_submission_returns_existing_product(self):
		payload = {
			"name": "Idempotent Item",
			"sale_price": "125.00",
			"cost_price": "100.00",
			"unit": "pcs",
			"stock": 0,
			"tax": "0.00",
			"warranty_months": 0,
			"low_stock_alert": 0,
		}

		first_response = self.client.post("/api/inventory/products/", payload, format='json')
		second_response = self.client.post("/api/inventory/products/", payload, format='json')

		self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(second_response.status_code, status.HTTP_200_OK)
		self.assertEqual(Product.objects.filter(created_by=self.tenant, name="Idempotent Item").count(), 1)
		self.assertEqual(first_response.data["id"], second_response.data["id"])


class ProductDeleteAPITests(TestCase):
	def setUp(self):
		cache.clear()
		self.client = APIClient()
		self.tenant = User.objects.create_user(
			username="tenant_delete",
			email="tenant.delete@test.com",
			password="testpassword",
			first_name="Tenant",
			last_name="Delete",
		)
		self.user = User.objects.create_user(
			username="user_delete",
			email="user.delete@test.com",
			password="testpassword",
			first_name="User",
			last_name="Delete",
			parent=self.tenant,
		)
		self.client.force_authenticate(user=self.user)

	def test_delete_unlinked_product_succeeds(self):
		product = Product.objects.create(
			name="Unlinked Product",
			sale_price=100,
			price=80,
			created_by=self.tenant,
		)
		res = self.client.delete(f"/api/inventory/products/{product.id}/")
		self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(Product.objects.filter(id=product.id).exists())

	def test_delete_protected_product_returns_400_with_helpful_message(self):
		from billing.models import SalesInvoice, SalesInvoiceItem
		product = Product.objects.create(
			name="Invoiced Product",
			sale_price=200,
			price=150,
			created_by=self.tenant,
		)
		invoice = SalesInvoice.objects.create(
			invoice_number="INV-DEL-001",
			invoice_date=date.today(),
			total_amount=200,
			created_by=self.tenant,
		)
		SalesInvoiceItem.objects.create(
			sales_invoice=invoice,
			product=product,
			quantity=1,
			price=200,
			amount=200,
		)

		res = self.client.delete(f"/api/inventory/products/{product.id}/")
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("Cannot delete 'Invoiced Product'", res.data.get("error", ""))
		self.assertIn("Sales Invoice Item", res.data.get("error", ""))
		# Verify product is preserved
		self.assertTrue(Product.objects.filter(id=product.id).exists())

	def test_bulk_delete_unlinked_products(self):
		p1 = Product.objects.create(name="Bulk 1", sale_price=50, created_by=self.tenant)
		p2 = Product.objects.create(name="Bulk 2", sale_price=60, created_by=self.tenant)

		res = self.client.post("/api/inventory/products/bulk-delete/", {"ids": [str(p1.id), str(p2.id)]}, format='json')
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		self.assertEqual(res.data.get("deleted_count"), 2)
		self.assertEqual(len(res.data.get("protected", [])), 0)
		self.assertFalse(Product.objects.filter(id__in=[p1.id, p2.id]).exists())

	def test_bulk_delete_mixed_protected_and_unlinked(self):
		from billing.models import SalesInvoice, SalesInvoiceItem
		p_free = Product.objects.create(name="Free Product", sale_price=50, created_by=self.tenant)
		p_locked = Product.objects.create(name="Locked Product", sale_price=60, created_by=self.tenant)

		invoice = SalesInvoice.objects.create(
			invoice_number="INV-DEL-002",
			invoice_date=date.today(),
			total_amount=60,
			created_by=self.tenant,
		)
		SalesInvoiceItem.objects.create(
			sales_invoice=invoice,
			product=p_locked,
			quantity=1,
			price=60,
			amount=60,
		)

		res = self.client.post("/api/inventory/products/bulk-delete/", {"ids": [str(p_free.id), str(p_locked.id)]}, format='json')
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		self.assertEqual(res.data.get("deleted_count"), 1)
		self.assertIn("Locked Product", res.data.get("protected", []))
		self.assertFalse(Product.objects.filter(id=p_free.id).exists())
		self.assertTrue(Product.objects.filter(id=p_locked.id).exists())

	def test_bulk_delete_all_protected_returns_400(self):
		from billing.models import SalesInvoice, SalesInvoiceItem
		p_locked = Product.objects.create(name="Only Locked", sale_price=75, created_by=self.tenant)

		invoice = SalesInvoice.objects.create(
			invoice_number="INV-DEL-003",
			invoice_date=date.today(),
			total_amount=75,
			created_by=self.tenant,
		)
		SalesInvoiceItem.objects.create(
			sales_invoice=invoice,
			product=p_locked,
			quantity=1,
			price=75,
			amount=75,
		)

		res = self.client.post("/api/inventory/products/bulk-delete/", {"ids": [str(p_locked.id)]}, format='json')
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(res.data.get("deleted_count"), 0)
		self.assertIn("Cannot delete selected products", res.data.get("error", ""))
		self.assertTrue(Product.objects.filter(id=p_locked.id).exists())


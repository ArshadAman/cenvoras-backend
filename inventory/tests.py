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

	def test_delete_protected_product_archives_gracefully(self):
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
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		self.assertTrue(res.data.get("archived"))
		# Verify product is preserved and marked inactive
		product.refresh_from_db()
		self.assertTrue(Product.objects.filter(id=product.id).exists())
		self.assertFalse(product.is_active)

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


class StockRecalculateAndAdjustmentTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.tenant = User.objects.create_user(
			username="tenant_stock",
			email="tenant.stock@test.com",
			password="testpassword",
			business_name="Stock Corp"
		)
		self.client.force_authenticate(user=self.tenant)

	def test_unbatched_product_recalculate_preserves_stock(self):
		product = Product.objects.create(
			name="Unbatched Gadget",
			sale_price=100.00,
			price=50.00,
			stock=42,
			created_by=self.tenant
		)
		recalculated = product.recalculate_stock(save=True)
		self.assertEqual(recalculated, 42)
		product.refresh_from_db()
		self.assertEqual(product.stock, 42)

	def test_batched_product_stock_point_signals_sync_stock(self):
		wh = Warehouse.objects.create(name="Central Warehouse", created_by=self.tenant)
		product = Product.objects.create(
			name="Batched Medicine",
			sale_price=200.00,
			price=120.00,
			stock=0,
			created_by=self.tenant
		)
		b1 = ProductBatch.objects.create(product=product, batch_number="B-1", sale_price=200.00)
		b2 = ProductBatch.objects.create(product=product, batch_number="B-2", sale_price=200.00)

		sp1 = StockPoint.objects.create(warehouse=wh, batch=b1, quantity=15)
		product.refresh_from_db()
		self.assertEqual(product.stock, 15)

		sp2 = StockPoint.objects.create(warehouse=wh, batch=b2, quantity=25)
		product.refresh_from_db()
		self.assertEqual(product.stock, 40)

		sp1.delete()
		product.refresh_from_db()
		self.assertEqual(product.stock, 25)

		sp2.delete()
		product.refresh_from_db()
		self.assertEqual(product.stock, 0)

	def test_stock_adjustment_api_flow_and_audit_journal(self):
		product = Product.objects.create(
			name="Adjustment Item",
			sale_price=50.00,
			price=25.00,
			stock=10,
			created_by=self.tenant
		)

		# Add 15
		res = self.client.post("/api/inventory/stock-adjustments/", {
			"product_id": str(product.id),
			"adjustment_type": "add",
			"quantity": 15,
			"reason": "received_shipment"
		}, format='json')
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		product.refresh_from_db()
		self.assertEqual(product.stock, 25)

		# Remove 5
		res = self.client.post("/api/inventory/stock-adjustments/", {
			"product_id": str(product.id),
			"adjustment_type": "remove",
			"quantity": 5,
			"reason": "damaged_goods"
		}, format='json')
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		product.refresh_from_db()
		self.assertEqual(product.stock, 20)

		# Set to 50
		res = self.client.post("/api/inventory/stock-adjustments/", {
			"product_id": str(product.id),
			"adjustment_type": "set",
			"quantity": 50,
			"reason": "inventory_count"
		}, format='json')
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		product.refresh_from_db()
		self.assertEqual(product.stock, 50)

		# Check GET adjustments history
		res_get = self.client.get(f"/api/inventory/stock-adjustments/?product_id={product.id}")
		self.assertEqual(res_get.status_code, status.HTTP_200_OK)
		self.assertEqual(len(res_get.data), 3)

	def test_reconcile_stock_command(self):
		from django.core.management import call_command
		import io

		wh = Warehouse.objects.create(name="Reconcile Warehouse", created_by=self.tenant)
		product = Product.objects.create(
			name="Reconcile Item",
			sale_price=10.00,
			stock=999,
			created_by=self.tenant
		)
		batch = ProductBatch.objects.create(product=product, batch_number="REC-1", sale_price=10.00)
		StockPoint.objects.create(warehouse=wh, batch=batch, quantity=50)

		# Desync manually
		Product.objects.filter(id=product.id).update(stock=999)

		out = io.StringIO()
		call_command('reconcile_stock', f'--product-id={product.id}', stdout=out)
		product.refresh_from_db()
		self.assertEqual(product.stock, 50)


class ProductSoftDeleteAndArchivalTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.tenant = User.objects.create_user(
			username="tenant_archive",
			email="tenant.archive@test.com",
			password="testpassword",
			business_name="Archive Corp"
		)
		self.client.force_authenticate(user=self.tenant)

	def test_unused_product_is_hard_deleted(self):
		product = Product.objects.create(
			name="Clean Product",
			sale_price=50.00,
			price=25.00,
			created_by=self.tenant
		)
		res = self.client.delete(f"/api/inventory/products/{product.id}/")
		self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(Product.objects.filter(id=product.id).exists())

	def test_linked_product_is_soft_deleted_as_archived(self):
		from billing.models import SalesInvoice, SalesInvoiceItem
		product = Product.objects.create(
			name="Protected Medicine",
			sale_price=100.00,
			price=60.00,
			created_by=self.tenant
		)
		invoice = SalesInvoice.objects.create(
			invoice_number="INV-ARCH-001",
			invoice_date=date.today(),
			total_amount=100,
			created_by=self.tenant,
		)
		SalesInvoiceItem.objects.create(
			sales_invoice=invoice,
			product=product,
			quantity=1,
			price=100,
			amount=100,
		)

		res = self.client.delete(f"/api/inventory/products/{product.id}/")
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		self.assertTrue(res.data.get("archived"))
		self.assertFalse(res.data.get("is_active"))

		# Product still exists in DB but is inactive
		product.refresh_from_db()
		self.assertTrue(Product.objects.filter(id=product.id).exists())
		self.assertFalse(product.is_active)

	def test_archived_product_hidden_from_default_list(self):
		p_active = Product.objects.create(
			name="Active Item",
			sale_price=50,
			is_active=True,
			created_by=self.tenant
		)
		p_archived = Product.objects.create(
			name="Archived Item",
			sale_price=50,
			is_active=False,
			created_by=self.tenant
		)

		# Default list: only active products
		res = self.client.get("/api/inventory/products/")
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		names = [item["name"] for item in res.data["results"]] if "results" in res.data else [item["name"] for item in res.data]
		self.assertIn("Active Item", names)
		self.assertNotIn("Archived Item", names)

		# With include_archived=true: both active and archived
		res_all = self.client.get("/api/inventory/products/?include_archived=true")
		self.assertEqual(res_all.status_code, status.HTTP_200_OK)
		names_all = [item["name"] for item in res_all.data["results"]] if "results" in res_all.data else [item["name"] for item in res_all.data]
		self.assertIn("Active Item", names_all)
		self.assertIn("Archived Item", names_all)

		# With is_active=false: only archived
		res_arch = self.client.get("/api/inventory/products/?is_active=false")
		self.assertEqual(res_arch.status_code, status.HTTP_200_OK)
		names_arch = [item["name"] for item in res_arch.data["results"]] if "results" in res_arch.data else [item["name"] for item in res_arch.data]
		self.assertNotIn("Active Item", names_arch)
		self.assertIn("Archived Item", names_arch)

	def test_archived_product_can_be_reactivated(self):
		product = Product.objects.create(
			name="Seasonal Item",
			sale_price=80,
			is_active=False,
			created_by=self.tenant
		)
		res = self.client.patch(
			f"/api/inventory/products/{product.id}/",
			{"is_active": True},
			format='json'
		)
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		product.refresh_from_db()
		self.assertTrue(product.is_active)

	def test_bulk_delete_with_archive_protected(self):
		from billing.models import SalesInvoice, SalesInvoiceItem
		p_free = Product.objects.create(name="Bulk Free Item", sale_price=20, created_by=self.tenant)
		p_linked = Product.objects.create(name="Bulk Linked Item", sale_price=30, created_by=self.tenant)

		invoice = SalesInvoice.objects.create(
			invoice_number="INV-BULK-001",
			invoice_date=date.today(),
			total_amount=30,
			created_by=self.tenant,
		)
		SalesInvoiceItem.objects.create(
			sales_invoice=invoice,
			product=p_linked,
			quantity=1,
			price=30,
			amount=30,
		)

		res = self.client.post(
			"/api/inventory/products/bulk-delete/",
			{"ids": [str(p_free.id), str(p_linked.id)], "archive_protected": True},
			format='json'
		)
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		self.assertEqual(res.data.get("deleted_count"), 1)
		self.assertEqual(res.data.get("archived_count"), 1)

		# p_free is truly deleted from DB
		self.assertFalse(Product.objects.filter(id=p_free.id).exists())
		# p_linked is archived in DB
		p_linked.refresh_from_db()
		self.assertFalse(p_linked.is_active)




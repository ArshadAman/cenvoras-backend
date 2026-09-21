from decimal import Decimal
from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from inventory.models import Product, Warehouse, ProductBatch, StockPoint
from billing.models import PurchaseBill, PurchaseBillItem
from reports.services import get_stock_valuation

User = get_user_model()


class StockValuationWACTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tenant_wac",
            email="tenant.wac@test.com",
            password="testpassword",
            business_name="WAC Enterprises"
        )
        self.client.force_authenticate(user=self.tenant)

    def test_wac_from_multiple_purchase_bills(self):
        """
        Verify WAC calculation when a product has multiple purchase bills with varying unit costs.
        100 @ 50 + 200 @ 65 = 18,000 / 300 = 60.00 per unit.
        """
        product = Product.objects.create(
            name="Raw Material Steel",
            sale_price=100.00,
            price=0.00,  # No static cost
            stock=0,  # Will be incremented by purchase bills
            created_by=self.tenant
        )

        bill1 = PurchaseBill.objects.create(
            bill_number="PB-001",
            bill_date=date.today(),
            vendor_name="Supplier A",
            total_amount=5000.00,
            created_by=self.tenant
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill1,
            product=product,
            quantity=100,
            price=Decimal('50.00'),
            amount=Decimal('5000.00')
        )

        bill2 = PurchaseBill.objects.create(
            bill_number="PB-002",
            bill_date=date.today(),
            vendor_name="Supplier B",
            total_amount=13000.00,
            created_by=self.tenant
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill2,
            product=product,
            quantity=200,
            price=Decimal('65.00'),
            amount=Decimal('13000.00')
        )

        report = get_stock_valuation(tenant=self.tenant)
        self.assertEqual(len(report['items']), 1)
        item = report['items'][0]
        self.assertEqual(item['name'], "Raw Material Steel")
        self.assertEqual(item['avg_cost'], Decimal('60.00'))
        self.assertEqual(item['total_value'], Decimal('18000.00'))
        self.assertEqual(item['valuation_method'], 'purchase_history_average')
        self.assertEqual(report['total_value'], Decimal('18000.00'))

    def test_wac_from_on_hand_stock_point_batches(self):
        """
        Verify WAC takes highest priority from active on-hand warehouse batches:
        Batch 1: 50 @ 100 = 5,000
        Batch 2: 50 @ 120 = 6,000
        WAC = 11,000 / 100 = 110.00 per unit.
        """
        wh = Warehouse.objects.create(name="Pharma Warehouse", created_by=self.tenant)
        product = Product.objects.create(
            name="Antibiotic Syrup",
            sale_price=150.00,
            price=90.00,
            stock=100,
            created_by=self.tenant
        )
        b1 = ProductBatch.objects.create(
            product=product,
            batch_number="ANT-001",
            cost_price=Decimal('100.00'),
            sale_price=Decimal('150.00')
        )
        b2 = ProductBatch.objects.create(
            product=product,
            batch_number="ANT-002",
            cost_price=Decimal('120.00'),
            sale_price=Decimal('150.00')
        )
        StockPoint.objects.create(warehouse=wh, batch=b1, quantity=50)
        StockPoint.objects.create(warehouse=wh, batch=b2, quantity=50)

        report = get_stock_valuation(tenant=self.tenant)
        self.assertEqual(len(report['items']), 1)
        item = report['items'][0]
        self.assertEqual(item['avg_cost'], Decimal('110.00'))
        self.assertEqual(item['total_value'], Decimal('11000.00'))
        self.assertEqual(item['valuation_method'], 'batch_moving_average')

    def test_wac_fallback_to_catalog_cost(self):
        """
        When no batches or purchases exist, fallback to opening catalog cost price.
        """
        product = Product.objects.create(
            name="Opening Stock Item",
            sale_price=50.00,
            price=Decimal('35.00'),
            stock=20,
            created_by=self.tenant
        )

        report = get_stock_valuation(tenant=self.tenant)
        self.assertEqual(len(report['items']), 1)
        item = report['items'][0]
        self.assertEqual(item['avg_cost'], Decimal('35.00'))
        self.assertEqual(item['total_value'], Decimal('700.00'))
        self.assertEqual(item['valuation_method'], 'catalog_cost')

    def test_archived_product_excluded_from_valuation(self):
        """
        Verify archived products are excluded from current inventory valuation.
        """
        Product.objects.create(
            name="Active Item",
            price=Decimal('20.00'),
            stock=10,
            is_active=True,
            created_by=self.tenant
        )
        Product.objects.create(
            name="Discontinued Item",
            price=Decimal('50.00'),
            stock=10,
            is_active=False,
            created_by=self.tenant
        )

        report = get_stock_valuation(tenant=self.tenant)
        names = [i['name'] for i in report['items']]
        self.assertIn("Active Item", names)
        self.assertNotIn("Discontinued Item", names)

    def test_stock_valuation_api_endpoint(self):
        """
        Test GET /api/reports/stock-valuation/ endpoint returns real WAC.
        """
        Product.objects.create(
            name="Endpoint Test Widget",
            sale_price=100.00,
            price=Decimal('45.00'),
            stock=10,
            created_by=self.tenant
        )

        res = self.client.get("/api/reports/stock-valuation/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("total_value", res.data)
        self.assertIn("items", res.data)
        self.assertEqual(len(res.data["items"]), 1)
        self.assertEqual(float(res.data["items"][0]["avg_cost"]), 45.0)
        self.assertEqual(float(res.data["items"][0]["total_value"]), 450.0)

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import SalesInvoice, Customer
from billing.models_sidecar import DeliveryChallan, DeliveryChallanItem, SalesOrder, SalesOrderItem
from inventory.models import Product, Warehouse
from decimal import Decimal
from datetime import date

User = get_user_model()

class DeliveryChallanFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tenant_challan",
            email="tenant_challan@test.com",
            password="testpassword"
        )
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Acme Construction",
            address="123 Industrial Area",
            gstin="29ABCDE1234F1Z5",
            created_by=self.tenant
        )

        self.warehouse = Warehouse.objects.create(
            name="Main Warehouse",
            address="Plot 4, Industrial Area",
            created_by=self.tenant
        )

        self.product = Product.objects.create(
            name="Cement Bag 50kg",
            sale_price=Decimal("400.00"),
            stock=100,
            unit="bag",
            tax=Decimal("18.00"),
            created_by=self.tenant
        )

    def test_create_delivery_challan_deducts_stock(self):
        """Creating a delivery challan should deduct stock upon dispatch without creating ledger entries."""
        initial_stock = self.product.stock  # 100
        url = "/api/billing/delivery-challans/"
        payload = {
            "date": str(date.today()),
            "customer": str(self.customer.id),
            "customer_name": self.customer.name,
            "vehicle_number": "KA-01-AB-1234",
            "warehouse": str(self.warehouse.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 20,
                    "price": 400.00,
                    "tax": 18.00,
                    "discount": 0,
                    "unit": "bag"
                }
            ]
        }
        res = self.client.post(url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        challan_id = res.data["id"]

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 20)  # Stock reduced by 20

        challan = DeliveryChallan.objects.get(id=challan_id)
        self.assertFalse(challan.is_billed)
        self.assertEqual(challan.status, 'open')
        self.assertEqual(challan.vehicle_number, "KA-01-AB-1234")
        self.assertTrue(challan.challan_number.startswith("DC-"))

    def test_convert_order_to_challan(self):
        """Converting a sales order to delivery challan copies items, reduces stock, and sets stage."""
        initial_stock = self.product.stock  # 100
        order = SalesOrder.objects.create(
            order_number="SO-TEST-001",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("8000.00"),
            created_by=self.tenant
        )
        SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=15,
            price=Decimal("400.00"),
            amount=Decimal("6000.00"),
            tax=Decimal("18.00"),
            discount=Decimal("0.00"),
            unit="bag"
        )

        res = self.client.post(f"/api/billing/sales-orders/{order.id}/convert_to_challan/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        challan_id = res.data["challan_id"]

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 15)  # Dispatched on challan

        order.refresh_from_db()
        self.assertEqual(order.stage, 'completed')  # Fully dispatched

        challan = DeliveryChallan.objects.get(id=challan_id)
        self.assertEqual(challan.sales_order_id, order.id)
        self.assertEqual(challan.items.count(), 1)
        self.assertEqual(challan.items.first().quantity, 15)

        # Check API serialization of sales order reference
        detail_res = self.client.get(f"/api/billing/delivery-challans/{challan_id}/")
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_res.data["sales_order_number"], order.order_number)
        self.assertIsNotNone(detail_res.data["sales_order_details"])
        self.assertEqual(detail_res.data["sales_order_details"]["order_number"], order.order_number)

    def test_convert_sales_order_partial_quantity_to_delivery_challan(self):
        """Converting partial quantity (e.g. 3 out of 6) must decrease order quantity and reduce stock accurately."""
        initial_stock = self.product.stock

        order = SalesOrder.objects.create(
            order_number="SO-PARTIAL-001",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("2400.00"),
            stage="new",
            created_by=self.tenant
        )
        order_item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=6,
            price=Decimal("400.00"),
            amount=Decimal("2400.00"),
            tax=Decimal("0.00"),
            discount=Decimal("0.00"),
            unit="pcs"
        )

        # Convert 3 out of 6 to Delivery Challan
        res = self.client.post(
            f"/api/billing/sales-orders/{order.id}/convert_to_challan/",
            data={"items": [{"id": order_item.id, "quantity": 3}]},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        challan_id = res.data["challan_id"]

        # 1. Stock reduced by 3
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 3)

        # 2. Challan has quantity 3
        challan = DeliveryChallan.objects.get(id=challan_id)
        self.assertEqual(challan.items.count(), 1)
        self.assertEqual(challan.items.first().quantity, 3)

        # 3. Order item quantity decreased to 3, stage is shipped (open for remaining)
        order.refresh_from_db()
        order_item.refresh_from_db()
        self.assertEqual(order_item.quantity, 3)
        self.assertEqual(order.total_amount, Decimal("1200.00"))
        self.assertEqual(order.stage, "shipped")

        # 4. Convert the remaining 3
        res2 = self.client.post(
            f"/api/billing/sales-orders/{order.id}/convert_to_challan/",
            data={"items": [{"id": order_item.id, "quantity": 3}]},
            format="json"
        )
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED, res2.data)

        order.refresh_from_db()
        self.assertEqual(order.stage, "completed")
        self.assertEqual(order.items.count(), 0)

    def test_convert_challan_to_invoice_no_double_deduction(self):
        """Converting a delivery challan to sales invoice must NOT double-deduct stock."""
        initial_stock = self.product.stock  # 100

        # Step 1: Create Challan
        challan = DeliveryChallan.objects.create(
            challan_number="DC-TEST-100",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("4720.00"),
            created_by=self.tenant
        )
        DeliveryChallanItem.objects.create(
            challan=challan,
            product=self.product,
            quantity=10,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("4720.00"),
            unit="bag"
        )
        # Deduct stock on challan creation manually or via serializer
        self.product.stock -= 10
        self.product.save(update_fields=['stock'])

        stock_after_challan = self.product.stock  # 90

        # Step 2: Convert to Invoice
        res = self.client.post(f"/api/billing/delivery-challans/{challan.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        invoice_id = res.data["invoice_id"]

        # Step 3: Verify stock did NOT reduce again
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_after_challan)  # Still 90! No double-deduction!

        # Step 4: Verify invoice created properly
        invoice = SalesInvoice.objects.get(id=invoice_id)
        self.assertEqual(invoice.challan_number, challan.challan_number)
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(invoice.items.first().quantity, 10)

        challan.refresh_from_db()
        self.assertTrue(challan.is_billed)
        self.assertEqual(challan.status, 'billed')
        self.assertEqual(challan.converted_invoice_id, invoice.id)

    def test_delete_unbilled_challan_restores_stock(self):
        """Deleting an unbilled delivery challan restores the dispatched stock."""
        initial_stock = self.product.stock  # 100

        res = self.client.post("/api/billing/delivery-challans/", {
            "date": str(date.today()),
            "customer": str(self.customer.id),
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 5,
                    "price": 400.00
                }
            ]
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        challan_id = res.data["id"]

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock - 5)

        del_res = self.client.delete(f"/api/billing/delivery-challans/{challan_id}/")
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock)  # Restored to 100!

    def test_delete_billed_challan_disallowed(self):
        """Attempting to delete an already-billed delivery challan must be rejected."""
        challan = DeliveryChallan.objects.create(
            challan_number="DC-BILLED-1",
            date=date.today(),
            customer=self.customer,
            is_billed=True,
            status='billed',
            created_by=self.tenant
        )
        res = self.client.delete(f"/api/billing/delivery-challans/{challan.id}/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

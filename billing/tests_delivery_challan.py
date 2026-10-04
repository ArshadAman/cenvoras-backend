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

        # Convert Challan to Invoice
        inv_res = self.client.post(f"/api/billing/delivery-challans/{challan_id}/convert_to_invoice/")
        self.assertEqual(inv_res.status_code, status.HTTP_200_OK, inv_res.data)

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

        # 3. Order item quantity remains 6, dispatched_quantity is 3, pending_quantity is 3
        order.refresh_from_db()
        order_item.refresh_from_db()
        self.assertEqual(order_item.quantity, 6)
        self.assertEqual(order_item.dispatched_quantity, 3)
        self.assertEqual(order_item.pending_quantity, 3)
        self.assertEqual(order.stage, "shipped")

        # 4. Convert the remaining 3
        res2 = self.client.post(
            f"/api/billing/sales-orders/{order.id}/convert_to_challan/",
            data={"items": [{"id": order_item.id, "quantity": 3}]},
            format="json"
        )
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED, res2.data)

        order.refresh_from_db()
        order_item.refresh_from_db()
        self.assertEqual(order.stage, "completed")
        self.assertEqual(order_item.quantity, 6)
        self.assertEqual(order_item.dispatched_quantity, 6)
        self.assertEqual(order_item.pending_quantity, 0)
        self.assertEqual(order.items.count(), 1)  # Items are NEVER deleted!

    def test_convert_sales_order_partial_quantity_to_sales_invoice(self):
        """Converting partial quantity (e.g. 2 out of 5) to Sales Invoice directly."""
        order = SalesOrder.objects.create(
            order_number="SO-INV-PARTIAL",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("2000.00"),
            stage="new",
            created_by=self.tenant
        )
        order_item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=5,
            price=Decimal("400.00"),
            amount=Decimal("2000.00"),
            tax=Decimal("0.00"),
            discount=Decimal("0.00"),
            unit="pcs"
        )

        res = self.client.post(
            f"/api/billing/sales-orders/{order.id}/convert_to_invoice/",
            data={"items": [{"id": order_item.id, "quantity": 2}]},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIn("invoice_number", res.data)

        order.refresh_from_db()
        order_item.refresh_from_db()
        self.assertEqual(order.stage, "shipped")
        self.assertEqual(order_item.quantity, 5)
        self.assertEqual(order_item.dispatched_quantity, 2)
        self.assertEqual(order_item.pending_quantity, 3)

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

    def test_bulk_convert_multiple_challans_to_single_invoice(self):
        """Converting multiple delivery challans consolidates them into a single SalesInvoice."""
        challan1 = DeliveryChallan.objects.create(
            challan_number="DC-BULK-001",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("1180.00"),
            created_by=self.tenant
        )
        DeliveryChallanItem.objects.create(
            challan=challan1,
            product=self.product,
            quantity=2,
            price=Decimal("500.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            unit="pcs"
        )

        challan2 = DeliveryChallan.objects.create(
            challan_number="DC-BULK-002",
            date=date.today(),
            customer=self.customer,
            total_amount=Decimal("1770.00"),
            created_by=self.tenant
        )
        DeliveryChallanItem.objects.create(
            challan=challan2,
            product=self.product,
            quantity=3,
            price=Decimal("500.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1770.00"),
            unit="pcs"
        )

        res = self.client.post("/api/billing/delivery-challans/bulk-convert-to-invoice/", {
            "challan_ids": [str(challan1.id), str(challan2.id)]
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIn("invoice_id", res.data)

        invoice_id = res.data["invoice_id"]
        from billing.models import SalesInvoice
        invoice = SalesInvoice.objects.get(id=invoice_id)

        # Verify challans are mentioned in challan_number
        self.assertIn("DC-BULK-001", invoice.challan_number)
        self.assertIn("DC-BULK-002", invoice.challan_number)
        self.assertEqual(invoice.items.count(), 2)

        # Verify challans are marked billed
        challan1.refresh_from_db()
        challan2.refresh_from_db()
        self.assertTrue(challan1.is_billed)
        self.assertTrue(challan2.is_billed)
        self.assertEqual(challan1.converted_invoice_id, invoice.id)
        self.assertEqual(challan2.converted_invoice_id, invoice.id)

    def test_convert_order_to_invoice_after_challan_dispatched(self):
        """Converting a fully dispatched sales order to sales invoice must succeed and link challans."""
        order = SalesOrder.objects.create(
            order_number="SO-POST-01",
            date=date.today(),
            customer=self.customer,
            stage="new",
            total_amount=Decimal("2360.00"),
            created_by=self.tenant
        )
        order_item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=4,
            dispatched_quantity=0,
            price=Decimal("500.00"),
            tax=Decimal("18.00"),
            amount=Decimal("2360.00"),
            unit="pcs"
        )

        # Convert full quantity to challan
        res_dc = self.client.post(f"/api/billing/sales-orders/{order.id}/convert_to_challan/", {
            "items": [{"id": order_item.id, "quantity": 4}]
        }, format="json")
        self.assertEqual(res_dc.status_code, status.HTTP_201_CREATED)

        order.refresh_from_db()
        self.assertEqual(order.stage, "completed")

        # Now convert the sales order to sales invoice post-delivery
        res_inv = self.client.post(f"/api/billing/sales-orders/{order.id}/convert_to_invoice/")
        self.assertEqual(res_inv.status_code, status.HTTP_200_OK, res_inv.data)

        from billing.models import SalesInvoice
        inv = SalesInvoice.objects.get(id=res_inv.data["invoice_id"])
        self.assertIn("DC-", inv.challan_number)
        self.assertEqual(inv.items.count(), 1)
        self.assertEqual(inv.items.first().quantity, 4)

    def test_delete_delivery_challan_restores_stock_and_so_dispatch(self):
        """Deleting a delivery challan must restore product stock and sales order item dispatched quantity."""
        order = SalesOrder.objects.create(
            order_number="SO-DEL-01",
            date=date.today(),
            customer=self.customer,
            stage="new",
            total_amount=Decimal("472.00"),
            created_by=self.tenant
        )
        so_item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=10,
            dispatched_quantity=0,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("472.00"),
            unit="bag"
        )
        # Create challan dispatching 4 bags
        challan = DeliveryChallan.objects.create(
            challan_number="DC-DEL-001",
            date=date.today(),
            customer=self.customer,
            sales_order=order,
            warehouse=self.warehouse,
            total_amount=Decimal("188.80"),
            status="open",
            created_by=self.tenant
        )
        DeliveryChallanItem.objects.create(
            challan=challan,
            product=self.product,
            quantity=4,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("188.80"),
            source_item_id=str(so_item.id)
        )
        so_item.dispatched_quantity = 4
        so_item.save(update_fields=['dispatched_quantity'])
        order.stage = 'shipped'
        order.save(update_fields=['stage'])

        # Stock before delete
        initial_stock = self.product.stock  # 100
        # Delete challan via API
        res = self.client.delete(f"/api/billing/delivery-challans/{challan.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Verify challan is deleted
        self.assertFalse(DeliveryChallan.objects.filter(id=challan.id).exists())

        # Verify Product stock is restored by 4
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, initial_stock + 4)

        # Verify SalesOrderItem dispatched_quantity is restored to 0
        so_item.refresh_from_db()
        self.assertEqual(so_item.dispatched_quantity, 0)

        # Verify SalesOrder stage is reverted to 'new'
        order.refresh_from_db()
        self.assertEqual(order.stage, 'new')

    def test_delete_sales_invoice_reverts_delivery_challan_to_open(self):
        """Deleting a sales invoice must revert the converted delivery challan back to is_billed=False and status='open'."""
        challan = DeliveryChallan.objects.create(
            challan_number="DC-REV-001",
            date=date.today(),
            customer=self.customer,
            warehouse=self.warehouse,
            total_amount=Decimal("472.00"),
            status="open",
            created_by=self.tenant
        )
        DeliveryChallanItem.objects.create(
            challan=challan,
            product=self.product,
            quantity=1,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("472.00")
        )

        # Convert to invoice
        res = self.client.post(f"/api/billing/delivery-challans/{challan.id}/convert_to_invoice/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        inv_id = res.data["invoice_id"]

        challan.refresh_from_db()
        self.assertTrue(challan.is_billed)
        self.assertEqual(challan.status, "billed")
        self.assertEqual(str(challan.converted_invoice_id), str(inv_id))

        # Now delete the Sales Invoice
        del_res = self.client.delete(f"/api/billing/sales-invoices/{inv_id}/edit/")
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)

        # Verify Delivery Challan is reverted back to open
        challan.refresh_from_db()
        self.assertFalse(challan.is_billed)
        self.assertEqual(challan.status, "open")
        self.assertIsNone(challan.converted_invoice)

    def test_edit_delivery_challan_updates_so_dispatch_and_stock(self):
        """Editing quantity on a delivery challan updates product stock and sales order item dispatched quantity."""
        order = SalesOrder.objects.create(
            order_number="SO-EDIT-01",
            date=date.today(),
            customer=self.customer,
            stage="shipped",
            total_amount=Decimal("4720.00"),
            created_by=self.tenant
        )
        so_item = SalesOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=10,
            dispatched_quantity=5,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("4720.00"),
            unit="bag"
        )
        challan = DeliveryChallan.objects.create(
            challan_number="DC-EDIT-001",
            date=date.today(),
            customer=self.customer,
            sales_order=order,
            warehouse=self.warehouse,
            total_amount=Decimal("2360.00"),
            status="open",
            created_by=self.tenant
        )
        dc_item = DeliveryChallanItem.objects.create(
            challan=challan,
            product=self.product,
            quantity=5,
            price=Decimal("400.00"),
            tax=Decimal("18.00"),
            amount=Decimal("2360.00"),
            source_item_id=str(so_item.id)
        )

        stock_before = self.product.stock  # 100

        # Edit challan to increase quantity to 8 (delta = +3)
        res = self.client.put(f"/api/billing/delivery-challans/{challan.id}/", {
            "date": str(date.today()),
            "customer": str(self.customer.id),
            "customer_name": self.customer.name,
            "warehouse": str(self.warehouse.id),
            "items": [
                {
                    "id": str(dc_item.id),
                    "product": str(self.product.id),
                    "quantity": 8,
                    "price": 400.00,
                    "tax": 18.00,
                    "source_item_id": str(so_item.id),
                }
            ]
        }, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        # Product stock should be decremented by 3
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_before - 3)

        # SalesOrderItem dispatched quantity should be updated to 8
        so_item.refresh_from_db()
        self.assertEqual(so_item.dispatched_quantity, 8)


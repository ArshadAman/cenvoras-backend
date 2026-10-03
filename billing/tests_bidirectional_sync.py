from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from inventory.models import Product, Warehouse
from billing.models import Customer, Vendor, SalesInvoice, SalesInvoiceItem, PurchaseOrder, PurchaseOrderItem
from billing.models_sidecar import Quotation, QuotationItem, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem
from billing.sync_service import DocumentSyncService

User = get_user_model()


class BidirectionalSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sync_tester', email='sync@test.com', password='password123')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.warehouse = Warehouse.objects.create(name='Main Hub', created_by=self.user)
        self.customer = Customer.objects.create(name='Acme Corp', created_by=self.user)
        self.vendor = Vendor.objects.create(name='Global Supplies', created_by=self.user)

        self.product1 = Product.objects.create(
            name='Industrial Widget',
            price=Decimal('100.00'),
            sale_price=Decimal('150.00'),
            stock=100,
            unit='pcs',
            created_by=self.user
        )
        self.product2 = Product.objects.create(
            name='Precision Bolt',
            price=Decimal('10.00'),
            sale_price=Decimal('20.00'),
            stock=500,
            unit='pcs',
            created_by=self.user
        )

    def test_full_chain_conversion_and_backward_item_alteration(self):
        """
        Tests:
        1. Quotation -> Sales Order -> Delivery Challan -> Sales Invoice
        2. Alter item in Sales Invoice
        3. Verify alterations propagate backward to Challan, Order, and Quotation.
        """
        # 1. Create Quotation
        quotation = Quotation.objects.create(
            quotation_number='QT-TEST-001',
            customer=self.customer,
            quotation_date='2026-10-04',
            status='approved',
            created_by=self.user
        )
        q_item1 = QuotationItem.objects.create(
            quotation=quotation,
            product=self.product1,
            quantity=10,
            price=Decimal('150.00'),
            amount=Decimal('1500.00'),
            unit='pcs'
        )
        q_item2 = QuotationItem.objects.create(
            quotation=quotation,
            product=self.product2,
            quantity=20,
            price=Decimal('20.00'),
            amount=Decimal('400.00'),
            unit='pcs'
        )
        DocumentSyncService.recalculate_document_total(quotation)
        self.assertEqual(quotation.total_amount, Decimal('1900.00'))

        # 2. Convert to Sales Order
        conv_so_res = self.client.post(f'/api/billing/quotations/{quotation.id}/convert-to-sales-order/')
        self.assertEqual(conv_so_res.status_code, status.HTTP_200_OK)
        sales_order = SalesOrder.objects.get(id=conv_so_res.data['sales_order_id'])
        so_items = list(sales_order.items.all())
        self.assertEqual(len(so_items), 2)
        so_item1 = next(item for item in so_items if item.product_id == self.product1.id)
        self.assertEqual(so_item1.source_item_id, str(q_item1.id))

        # 3. Convert to Delivery Challan
        conv_dc_res = self.client.post(f'/api/billing/sales-orders/{sales_order.id}/convert_to_challan/')
        self.assertEqual(conv_dc_res.status_code, status.HTTP_201_CREATED)
        challan = DeliveryChallan.objects.get(id=conv_dc_res.data['challan_id'])
        dc_items = list(challan.items.all())
        self.assertEqual(len(dc_items), 2)
        dc_item1 = next(item for item in dc_items if item.product_id == self.product1.id)
        self.assertEqual(dc_item1.source_item_id, str(so_item1.id))

        # 4. Convert Delivery Challan to Sales Invoice
        conv_inv_res = self.client.post(f'/api/billing/delivery-challans/{challan.id}/convert_to_invoice/')
        self.assertEqual(conv_inv_res.status_code, status.HTTP_200_OK)
        invoice = SalesInvoice.objects.get(id=conv_inv_res.data['invoice_id'])
        inv_items = list(invoice.items.all())
        self.assertEqual(len(inv_items), 2)
        inv_item1 = next(item for item in inv_items if item.product_id == self.product1.id)
        self.assertEqual(inv_item1.source_item_id, str(dc_item1.id))

        # 5. BACKWARD SYNC: Alter item in Sales Invoice (modify quantity of product1 from 10 to 4)
        patch_payload = {
            'items': [
                {
                    'id': str(inv_item1.id),
                    'source_item_id': str(inv_item1.source_item_id),
                    'product': str(self.product1.id),
                    'quantity': 4,
                    'price': '150.00',
                    'unit': 'pcs',
                },
                {
                    'id': str(inv_items[1].id),
                    'source_item_id': str(inv_items[1].source_item_id),
                    'product': str(self.product2.id),
                    'quantity': 20,
                    'price': '20.00',
                    'unit': 'pcs',
                }
            ]
        }
        res_edit = self.client.patch(f'/api/billing/sales-invoices/{invoice.id}/edit/', patch_payload, format='json')
        self.assertEqual(res_edit.status_code, status.HTTP_200_OK, res_edit.data)

        # Reload invoice and check new total: 4 * 150 + 20 * 20 = 600 + 400 = 1000.00
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal('1000.00'))

        # Verify Delivery Challan updated backward
        challan.refresh_from_db()
        dc_item1.refresh_from_db()
        self.assertEqual(dc_item1.quantity, 4)
        self.assertEqual(challan.total_amount, Decimal('1000.00'))

        # Verify Sales Order updated backward
        sales_order.refresh_from_db()
        so_item1.refresh_from_db()
        self.assertEqual(so_item1.quantity, 4)
        self.assertEqual(sales_order.total_amount, Decimal('1000.00'))

        # Verify Quotation updated backward
        quotation.refresh_from_db()
        q_item1.refresh_from_db()
        self.assertEqual(q_item1.quantity, 4)
        self.assertEqual(quotation.total_amount, Decimal('1000.00'))

    def test_backward_item_deletion_and_addition(self):
        """
        Tests deleting a line item and adding a new line item in Sales Invoice,
        verifying the deletion and addition sync back to earlier stages.
        """
        # Create Quotation -> Order -> Challan -> Invoice chain
        quotation = Quotation.objects.create(
            quotation_number='QT-TEST-002',
            customer=self.customer,
            quotation_date='2026-10-04',
            status='approved',
            created_by=self.user
        )
        q_item1 = QuotationItem.objects.create(
            quotation=quotation, product=self.product1, quantity=5, price=Decimal('100.00'), amount=Decimal('500.00')
        )
        q_item2 = QuotationItem.objects.create(
            quotation=quotation, product=self.product2, quantity=10, price=Decimal('10.00'), amount=Decimal('100.00')
        )
        DocumentSyncService.recalculate_document_total(quotation)

        # Convert to Order
        res_so = self.client.post(f'/api/billing/quotations/{quotation.id}/convert-to-sales-order/')
        so = SalesOrder.objects.get(id=res_so.data['sales_order_id'])

        # Convert to Challan
        res_dc = self.client.post(f'/api/billing/sales-orders/{so.id}/convert_to_challan/')
        dc = DeliveryChallan.objects.get(id=res_dc.data['challan_id'])

        # Convert to Invoice
        res_inv = self.client.post(f'/api/billing/delivery-challans/{dc.id}/convert_to_invoice/')
        inv = SalesInvoice.objects.get(id=res_inv.data['invoice_id'])
        inv_item1 = inv.items.filter(product=self.product1).first()

        # New product to add
        product3 = Product.objects.create(
            name='Extra Bolt', price=Decimal('5.00'), sale_price=Decimal('8.00'), stock=100, created_by=self.user
        )

        # Edit Invoice: Delete product2 line, keep product1 line, add product3 line
        patch_payload = {
            'items': [
                {
                    'id': str(inv_item1.id),
                    'source_item_id': str(inv_item1.source_item_id),
                    'product': str(self.product1.id),
                    'quantity': 5,
                    'price': '100.00',
                    'unit': 'pcs',
                },
                {
                    'product': str(product3.id),
                    'quantity': 2,
                    'price': '8.00',
                    'unit': 'pcs',
                }
            ]
        }
        res_edit = self.client.patch(f'/api/billing/sales-invoices/{inv.id}/edit/', patch_payload, format='json')
        self.assertEqual(res_edit.status_code, status.HTTP_200_OK)

        # Verify deletion and addition in invoice: 5 * 100 + 2 * 8 = 516.00
        inv.refresh_from_db()
        self.assertEqual(inv.items.count(), 2)
        self.assertEqual(inv.total_amount, Decimal('516.00'))

        # Verify deletion of product2 and addition of product3 in Delivery Challan
        dc.refresh_from_db()
        self.assertEqual(dc.items.count(), 2)
        self.assertFalse(dc.items.filter(product=self.product2).exists())
        self.assertTrue(dc.items.filter(product=product3).exists())
        self.assertEqual(dc.total_amount, Decimal('516.00'))

        # Verify Sales Order
        so.refresh_from_db()
        self.assertEqual(so.items.count(), 2)
        self.assertFalse(so.items.filter(product=self.product2).exists())
        self.assertTrue(so.items.filter(product=product3).exists())
        self.assertEqual(so.total_amount, Decimal('516.00'))

        # Verify Quotation
        quotation.refresh_from_db()
        self.assertEqual(quotation.items.count(), 2)
        self.assertFalse(quotation.items.filter(product=self.product2).exists())
        self.assertTrue(quotation.items.filter(product=product3).exists())
        self.assertEqual(quotation.total_amount, Decimal('516.00'))

    def test_forward_propagation_from_quotation(self):
        """
        Tests editing an item in Quotation propagates forward to Sales Order.
        """
        quotation = Quotation.objects.create(
            quotation_number='QT-TEST-003',
            customer=self.customer,
            quotation_date='2026-10-04',
            status='approved',
            created_by=self.user
        )
        q_item = QuotationItem.objects.create(
            quotation=quotation, product=self.product1, quantity=10, price=Decimal('150.00'), amount=Decimal('1500.00')
        )
        DocumentSyncService.recalculate_document_total(quotation)

        res_so = self.client.post(f'/api/billing/quotations/{quotation.id}/convert-to-sales-order/')
        so = SalesOrder.objects.get(id=res_so.data['sales_order_id'])

        # Now edit Quotation: change price from 150 to 180 and quantity from 10 to 8
        patch_payload = {
            'items': [
                {
                    'id': q_item.id,
                    'product': self.product1.id,
                    'quantity': 8,
                    'price': '180.00',
                    'unit': 'pcs',
                }
            ]
        }
        res_q_edit = self.client.patch(f'/api/billing/quotations/{quotation.id}/', patch_payload, format='json')
        self.assertEqual(res_q_edit.status_code, status.HTTP_200_OK, res_q_edit.data)
        quotation.refresh_from_db()
        self.assertEqual(quotation.total_amount, Decimal('1440.00'))

        # Check Sales Order was updated forward:
        so.refresh_from_db()
        so_item = so.items.first()
        self.assertEqual(so_item.quantity, 8)
        self.assertEqual(so_item.price, Decimal('180.00'))
        self.assertEqual(so.total_amount, Decimal('1440.00'))

    def test_sales_order_to_purchase_order_conversion(self):
        """
        Tests:
        1. SalesOrder -> PurchaseOrder conversion
        2. Verify purchase order items link to sales order items
        3. Alter sales order item, verify purchase order item updates
        """
        order = SalesOrder.objects.create(
            order_number='SO-TEST-PO',
            customer=self.customer,
            date='2026-10-04',
            total_amount=Decimal('300.00'),
            created_by=self.user
        )
        so_item = SalesOrderItem.objects.create(
            order=order, product=self.product1, quantity=2, price=Decimal('150.00'), amount=Decimal('300.00')
        )

        res_po = self.client.post(f'/api/billing/sales-orders/{order.id}/convert_to_purchase_order/', {
            'vendor_id': str(self.vendor.id),
        })
        self.assertEqual(res_po.status_code, status.HTTP_201_CREATED, res_po.data)

        po = PurchaseOrder.objects.get(id=res_po.data['purchase_order_id'])
        self.assertEqual(po.source_sales_order_id, order.id)
        po_item = po.items.first()
        self.assertEqual(po_item.source_item_id, str(so_item.id))
        self.assertEqual(po_item.quantity, 2)
        # Cost price of product1 is 100.00, so po total should be 2 * 100 = 200.00
        self.assertEqual(po.total_amount, Decimal('200.00'))

        # Update Sales Order item: change quantity to 5
        patch_payload = {
            'items': [
                {
                    'id': so_item.id,
                    'product': self.product1.id,
                    'quantity': 5,
                    'price': '150.00',
                    'unit': 'pcs',
                }
            ]
        }
        res_so_edit = self.client.patch(f'/api/billing/sales-orders/{order.id}/', patch_payload, format='json')
        self.assertEqual(res_so_edit.status_code, status.HTTP_200_OK, res_so_edit.data)

        # Verify PO item quantity updated to 5
        po.refresh_from_db()
        po_item.refresh_from_db()
        self.assertEqual(po_item.quantity, 5)
        self.assertEqual(po.total_amount, Decimal('500.00'))

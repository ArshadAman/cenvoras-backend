from decimal import Decimal
from datetime import date
from unittest.mock import Mock
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import Customer
from billing.models_sidecar import Quotation, QuotationItem, SalesOrder, DeliveryChallan
from billing.views_sidecar import _validate_converted_quotation_items
from inventory.models import Product

User = get_user_model()

class QuotationConversionEnhancementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='quotation_tester',
            email='quotation@test.com',
            password='testpassword123',
            quotation_prefix='QUOT-'
        )
        self.client.force_authenticate(user=self.user)
        self.customer = Customer.objects.create(
            name='Precision Labs',
            email='labs@precision.com',
            created_by=self.user
        )
        self.product = Product.objects.create(
            name='Microscope Specimen',
            price=Decimal('200.00'),
            unit='pcs',
            stock=100,
            created_by=self.user
        )

    def test_quotation_prefix_generation(self):
        from billing.sequence_service import allocate_next_number
        number = allocate_next_number(self.user, document_type='quotation')
        self.assertTrue(number.startswith('QUOT-'))

    def test_convert_with_qty_discount_and_po_details(self):
        quotation = Quotation.objects.create(
            quotation_number='QUOT-001',
            customer=self.customer,
            quotation_date=date(2026, 10, 4),
            status='approved',
            created_by=self.user
        )
        q_item = QuotationItem.objects.create(
            quotation=quotation,
            product=self.product,
            quantity=10,
            price=Decimal('200.00'),
            amount=Decimal('2000.00'),
            approval_status='approved'
        )

        payload = {
            'po_number': 'PO-CUST-8832',
            'po_date': '2026-10-04',
            'items': [
                {
                    'id': str(q_item.id),
                    'quantity': 3,
                    'discount': 10.0,  # 10% extra discount
                    'price': '200.00'
                }
            ]
        }

        res = self.client.post(
            f'/api/billing/quotations/{quotation.id}/convert-to-sales-order/',
            payload,
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        order_id = res.data['sales_order_id']
        order = SalesOrder.objects.get(id=order_id)

        # Check PO details saved
        self.assertEqual(order.po_number, 'PO-CUST-8832')
        self.assertEqual(str(order.po_date), '2026-10-04')

        # Check item overrides: 3 pcs * 200 * (1 - 0.10) = 540.00
        so_item = order.items.first()
        self.assertEqual(so_item.quantity, 3)
        self.assertEqual(so_item.discount, Decimal('10.00'))
        self.assertEqual(so_item.amount, Decimal('540.00'))
        self.assertEqual(order.total_amount, Decimal('540.00'))

        # Now test forwarding PO details to delivery challan
        res_dc = self.client.post(f'/api/billing/sales-orders/{order.id}/convert_to_challan/', {})
        self.assertEqual(res_dc.status_code, status.HTTP_201_CREATED, res_dc.data)
        challan_id = res_dc.data.get('challan_id') or res_dc.data.get('id')
        dc = DeliveryChallan.objects.get(id=challan_id)
        self.assertEqual(dc.po_number, 'PO-CUST-8832')
        self.assertEqual(str(dc.po_date), '2026-10-04')

    def _create_quotation(self, status='approved', quantity=10):
        quotation = Quotation.objects.create(
            quotation_number=f'QUOT-{status[:3].upper()}-{quantity}',
            customer=self.customer,
            customer_name=self.customer.name,
            quotation_date=date(2026, 10, 4),
            status=status,
            created_by=self.user,
        )
        item = QuotationItem.objects.create(
            quotation=quotation,
            product=self.product,
            quantity=quantity,
            price=Decimal('200.00'),
            amount=Decimal(str(200 * quantity)),
            approval_status='approved',
        )
        return quotation, item

    def _convert_quotation(self, quotation, item):
        res = self.client.post(
            f'/api/billing/quotations/{quotation.id}/convert-to-sales-order/',
            {'items': [{'id': str(item.id), 'quantity': item.quantity}]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        quotation.refresh_from_db()
        return res

    def test_converted_quotation_cannot_be_deleted(self):
        quotation, item = self._create_quotation()
        self._convert_quotation(quotation, item)

        res = self.client.delete(f'/api/billing/quotations/{quotation.id}/')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cannot be deleted', res.data['message'])
        self.assertTrue(Quotation.objects.filter(pk=quotation.pk).exists())

    def test_converted_quotation_can_edit_header_and_increase_qty(self):
        quotation, item = self._create_quotation()
        self._convert_quotation(quotation, item)

        header_res = self.client.patch(
            f'/api/billing/quotations/{quotation.id}/',
            {'delivery_address': 'Warehouse B, Dock 4'},
            format='json',
        )
        self.assertEqual(header_res.status_code, status.HTTP_200_OK, header_res.data)
        quotation.refresh_from_db()
        self.assertEqual(quotation.delivery_address, 'Warehouse B, Dock 4')

        increase_res = self.client.patch(
            f'/api/billing/quotations/{quotation.id}/',
            {
                'items': [
                    {
                        'id': item.id,
                        'product': self.product.id,
                        'quantity': 15,
                        'price': '200.00',
                    }
                ]
            },
            format='json',
        )
        self.assertEqual(increase_res.status_code, status.HTTP_200_OK, increase_res.data)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 15)

    def test_converted_quotation_cannot_decrease_qty_or_remove_item(self):
        quotation, item = self._create_quotation()
        self._convert_quotation(quotation, item)

        decrease_res = self.client.patch(
            f'/api/billing/quotations/{quotation.id}/',
            {
                'items': [
                    {
                        'id': item.id,
                        'product': self.product.id,
                        'quantity': 4,
                        'price': '200.00',
                    }
                ]
            },
            format='json',
        )
        self.assertEqual(decrease_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('decrease', decrease_res.data['message'])
        item.refresh_from_db()
        self.assertEqual(item.quantity, 10)

        remove_res = self.client.patch(
            f'/api/billing/quotations/{quotation.id}/',
            {'items': []},
            format='json',
        )
        self.assertEqual(remove_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('remove', remove_res.data['message'])
        self.assertTrue(quotation.items.filter(pk=item.pk).exists())

    def test_unconverted_quotation_can_be_deleted_and_items_reduced(self):
        quotation, item = self._create_quotation(status='approved', quantity=8)

        reduce_res = self.client.patch(
            f'/api/billing/quotations/{quotation.id}/',
            {
                'items': [
                    {
                        'id': item.id,
                        'product': self.product.id,
                        'quantity': 3,
                        'price': '200.00',
                    }
                ]
            },
            format='json',
        )
        self.assertEqual(reduce_res.status_code, status.HTTP_200_OK, reduce_res.data)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 3)

        delete_res = self.client.delete(f'/api/billing/quotations/{quotation.id}/')
        self.assertEqual(delete_res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Quotation.objects.filter(pk=quotation.pk).exists())


def _mock_quotation_with_item(quantity=10, status='converted'):
    item = Mock(id=11, quantity=quantity, row_type='item')
    quotation = Mock(status=status)
    quotation.sales_orders.exists.return_value = status in ('converted', 'partially_converted')
    quotation.items.all.return_value = [item]
    return quotation, item


class ConvertedQuotationItemValidationTests(SimpleTestCase):
    def test_header_only_patch_is_allowed(self):
        quotation, _item = _mock_quotation_with_item()
        self.assertIsNone(_validate_converted_quotation_items(quotation, {
            'delivery_address': 'Dock 4',
        }))

    def test_quantity_increase_is_allowed(self):
        quotation, _item = _mock_quotation_with_item()
        self.assertIsNone(_validate_converted_quotation_items(quotation, {
            'items': [{'id': 11, 'quantity': 15, 'row_type': 'item'}],
        }))

    def test_quantity_decrease_is_rejected(self):
        quotation, _item = _mock_quotation_with_item()
        res = _validate_converted_quotation_items(quotation, {
            'items': [{'id': 11, 'quantity': 4, 'row_type': 'item'}],
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('decrease', res.data['message'])

    def test_item_removal_is_rejected(self):
        quotation, _item = _mock_quotation_with_item()
        res = _validate_converted_quotation_items(quotation, {'items': []})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('remove', res.data['message'])

    def test_unconverted_quotation_is_not_restricted(self):
        quotation, _item = _mock_quotation_with_item(status='approved')
        self.assertIsNone(_validate_converted_quotation_items(quotation, {
            'items': [{'id': 11, 'quantity': 1, 'row_type': 'item'}],
        }))

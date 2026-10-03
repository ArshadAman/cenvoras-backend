from decimal import Decimal
from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from billing.models import Customer
from billing.models_sidecar import Quotation, QuotationItem, SalesOrder, DeliveryChallan
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

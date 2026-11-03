"""UAT P1 item 13 (Rule 46 invoice essentials) - Rudra CA."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from billing.rule46 import rule46_errors, serial_error
from billing.tests_four_fixes import FourFixesBase
from users.models import User


class SerialTests(SimpleTestCase):
    def test_serial(self):
        self.assertIsNone(serial_error('INV-0001'))
        self.assertIsNone(serial_error('INV/26-27/0001'))
        self.assertIn('16', serial_error('INV-2026-27-000001'))
        self.assertIn('capital letters', serial_error('inv-0001'))
        self.assertIn('capital letters', serial_error('INV 0001'))

    def test_recipient_and_lines(self):
        base = dict(invoice_number='INV-1', invoice_date=date(2026, 10, 1), customer_name='A',
                    customer_address='', customer_gstin='', items=[{'quantity': 1, 'unit': 'pcs'}],
                    taxable_total=Decimal('49999.99'))
        self.assertEqual(rule46_errors(**base), {})
        self.assertIn('customer_address', rule46_errors(**{**base, 'taxable_total': Decimal('50000')}))
        self.assertIn('customer_address', rule46_errors(**{**base, 'customer_gstin': '29AAGCB7383J1Z4'}))
        self.assertIn('customer_name', rule46_errors(**{**base, 'customer_name': ' '}))
        self.assertIn('invoice_date', rule46_errors(**{**base, 'invoice_date': None}))
        errs = rule46_errors(**{**base, 'items': [{'quantity': 0, 'unit': ''}]})
        self.assertEqual(len(errs['items']), 2)
        self.assertEqual(rule46_errors(**{**base, 'invoice_number': 'DFT-0001', 'items': [{'row_type': 'note'}]}), {})


class Rule46ApiTests(FourFixesBase):
    def _post(self, **extra):
        payload = {"customer_name": self.customer.name, "invoice_date": str(date.today()), "status": "final",
                   "place_of_supply": "Karnataka", "customer_address": "MG Road, Bengaluru",
                   "items": [{"product": str(self.product.id), "quantity": 1, "price": "100.00", "tax": "18.00",
                              "amount": "0", "hsn_sac_code": "847130", "unit": "pcs"}]}
        payload.update(extra)
        return self.client.post("/api/billing/sales-invoices/", payload, format="json")

    def test_ok_and_rcm_flag_saved(self):
        res = self._post(is_reverse_charge=True)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['is_reverse_charge'])

    def test_long_or_bad_serial_rejected(self):
        res = self._post(invoice_number='INVOICE-2026-000001')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('16', str(res.data))

    def test_registered_recipient_needs_address(self):
        self.customer.address = ''
        self.customer.save()
        res = self._post(customer_address='')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('address', str(res.data))


class CompanyGstinSetupTests(APITestCase):
    def test_gstin_required_for_india_setup(self):
        u = User.objects.create_user(username='g1', email='g1@example.com', password='Secret123!', business_name='X')
        self.client.force_authenticate(u)
        res = self.client.patch('/api/users/profile/setup/', {'gstin': ''}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('GSTIN is required', str(res.data))

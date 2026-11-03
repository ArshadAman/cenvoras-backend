"""UAT P1 item 13 (Rule 46 invoice essentials) - Rudra CA."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from billing.rule46 import rule46_errors, serial_error
from billing.models import SalesInvoice
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


class GstStatusTests(FourFixesBase):
    """UAT P1 #13 rework (Rudra CA): Regular / Composition / Not registered."""
    VALID = '29AAGCB7383J1Z4'

    def setUp(self):
        super().setUp()
        from datetime import timedelta
        from django.utils import timezone
        from subscription.models import TenantSubscription
        # FourFixesBase stores a date in a DateTimeField; the profile endpoint needs a datetime.
        TenantSubscription.objects.filter(tenant=self.tenant).update(
            current_period_end=timezone.now() + timedelta(days=365))
        self.tenant = User.objects.get(pk=self.tenant.pk)   # drop the cached subscription
        self.client.force_authenticate(user=self.tenant)

    def _inv(self, **extra):
        payload = {"customer_name": self.customer.name, "invoice_date": str(date.today()), "status": "final",
                   "place_of_supply": "Karnataka", "customer_address": "MG Road, Bengaluru",
                   "items": [{"product": str(self.product.id), "quantity": 1, "price": "100.00", "tax": "18.00",
                              "amount": "0", "hsn_sac_code": "847130", "unit": "pcs"}]}
        payload.update(extra)
        return self.client.post("/api/billing/sales-invoices/", payload, format="json")

    def _set(self, status, gstin):
        self.tenant.gst_status = status
        self.tenant.gstin = gstin
        self.tenant.save()

    def _profile(self):
        res = self.client.get('/api/users/profile/')
        self.assertEqual(res.status_code, 200)
        return res.data['billing_profile']

    def test_setup_no_longer_requires_gstin(self):
        u = User.objects.create_user(username='g1', email='g1@example.com', password='Secret123!', business_name='X')
        self.client.force_authenticate(u)
        res = self.client.patch('/api/users/profile/setup/', {
            'gstin': '', 'business_name': 'X', 'business_address': 'Somewhere 1'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        u.refresh_from_db()
        self.assertEqual(u.gst_status, 'unregistered')   # derived from data on setup

    def test_registered_status_needs_gstin_and_unregistered_has_none(self):
        res = self.client.patch('/api/users/profile/update/', {'gst_status': 'composition', 'gstin': ''},
                                format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('GSTIN is required', str(res.data))
        res = self.client.patch('/api/users/profile/update/', {'gst_status': 'unregistered'}, format='json')
        self.assertEqual(res.status_code, 400, res.data)    # tenant still has a GSTIN
        res = self.client.patch('/api/users/profile/update/', {'gst_status': 'unregistered', 'gstin': ''},
                                format='json')
        self.assertIn(res.status_code, (200, 201), res.data)

    def test_regular(self):
        self._set('regular', self.VALID)
        p = self._profile()
        self.assertEqual((p['gst_status'], p['document_title'], p['tax_locked'], p['gst_returns_available']),
                         ('regular', 'Tax Invoice', False, True))
        res = self._inv()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(SalesInvoice.objects.get(pk=res.data['id']).items.get().tax, Decimal('18.00'))
        self.assertEqual(self._inv(invoice_number='INVOICE-2026-000001').status_code, 400)  # Rule 46 on
        d = str(date.today())
        self.assertEqual(self.client.get(f'/api/billing/gst/gstr3b/?from={d}&to={d}').status_code, 200)

    def test_composition_bill_of_supply(self):
        self._set('composition', self.VALID)
        p = self._profile()
        self.assertEqual(p['document_title'], 'Bill of Supply')
        self.assertEqual(p['document_declaration'],
                         'composition taxable person, not eligible to collect tax on supplies')
        self.assertTrue(p['tax_locked'])
        self.assertFalse(p['gst_returns_available'])
        res = self._inv()
        self.assertEqual(res.status_code, 201, res.data)
        inv = SalesInvoice.objects.get(pk=res.data['id'])
        self.assertEqual(inv.items.get().tax, Decimal('0.00'))
        self.assertEqual(inv.total_amount, Decimal('100.00'))
        d = str(date.today())
        self.assertEqual(self.client.get(f'/api/billing/gst/gstr3b/?from={d}&to={d}').status_code, 403)
        self.assertEqual(self.client.get(f'/api/billing/gst/gstr1-export/?from={d}&to={d}').status_code, 403)

    def test_not_registered_invoice(self):
        self._set('unregistered', '')
        p = self._profile()
        self.assertEqual(p['document_title'], 'Invoice')
        self.assertNotEqual(p['document_title'], 'Tax Invoice')
        self.assertTrue(p['tax_locked'])
        self.assertFalse(p['gst_returns_available'])
        res = self._inv()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(SalesInvoice.objects.get(pk=res.data['id']).items.get().tax, Decimal('0.00'))

    def test_s22_reminder_for_not_registered(self):
        from billing.gst_thresholds import gst_shield
        self._set('unregistered', '')
        out = gst_shield(self.tenant, date.today())
        self.assertFalse(out['registered'])
        self.assertIn('registration_threshold', out)

    def test_unset_status_keeps_legacy_behaviour(self):
        from billing import gst_status as g
        self._set(None, '')
        self.assertEqual(g.gst_status(self.tenant), 'unregistered')   # shown, derived from data
        self.assertFalse(g.tax_locked(self.tenant))                    # but nothing locked/hidden
        self.assertTrue(g.gst_returns_available(self.tenant))

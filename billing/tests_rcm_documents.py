"""RCM self-invoice (s.31(3)(f), Rule 47A) and payment voucher (s.31(3)(g)) - Rudra follow-up to #7."""
from datetime import date, timedelta
from decimal import Decimal

from billing.models_rcm import RcmPaymentVoucher, RcmSelfInvoice
from billing.tests_uat_p1_itc_rcm import ItcRcmTests


class RcmDocumentTests(ItcRcmTests):
    def _rcm_bill(self, vendor=None, bill_date=None):
        extra = {'bill_date': str(bill_date)} if bill_date else {}
        return self._post(vendor or self.unreg, [self._line(price='10000', tax='5', is_rcm=True)], **extra)

    def test_self_invoice_unregistered_rcm(self):
        bill = self._rcm_bill()
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        si = RcmSelfInvoice.objects.get(purchase_bill=bill)
        self.assertTrue(si.number.startswith('RCM-'))
        self.assertLessEqual(len(si.number), 16)
        self.assertEqual((si.taxable_value, si.cgst, si.sgst, si.igst),
                         (Decimal('10000.00'), Decimal('250.00'), Decimal('250.00'), Decimal('0.00')))
        # one per bill
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_own_series_increments(self):
        b1, b2 = self._rcm_bill(), self._rcm_bill()
        n1 = self.client.post(f'/api/billing/purchase-bills/{b1.id}/rcm-self-invoice/', {}, format='json').data['number']
        n2 = self.client.post(f'/api/billing/purchase-bills/{b2.id}/rcm-self-invoice/', {}, format='json').data['number']
        self.assertNotEqual(n1, n2)
        self.assertEqual(int(n2[-4:]), int(n1[-4:]) + 1)

    def test_rule_47a_30_days(self):
        old = date.today() - timedelta(days=40)
        bill = self._rcm_bill(bill_date=old)
        lst = self.client.get('/api/billing/rcm/self-invoices/').data
        due = [d for d in lst['due'] if d['purchase_bill'] == str(bill.id)]
        self.assertTrue(due and due[0]['overdue'])
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('30 days', res.data['error'])
        ok = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/',
                              {'date': str(old + timedelta(days=30))}, format='json')
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_no_self_invoice_for_registered_supplier_or_non_rcm(self):
        reg_rcm = self._rcm_bill(vendor=self.reg)
        res = self.client.post(f'/api/billing/purchase-bills/{reg_rcm.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        plain = self._post(self.reg, [self._line()])
        res = self.client.post(f'/api/billing/purchase-bills/{plain.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_payment_voucher(self):
        bill = self._rcm_bill(vendor=self.reg)   # registered GTA under RCM still needs a voucher
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-payment-voucher/',
                               {'amount_paid': '10000.00'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        v = RcmPaymentVoucher.objects.get(purchase_bill=bill)
        self.assertTrue(v.number.startswith('RPV-'))
        self.assertEqual(v.amount_paid, Decimal('10000.00'))
        self.assertEqual(v.cgst + v.sgst + v.igst, Decimal('500.00'))
        bad = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-payment-voucher/',
                               {'amount_paid': '0'}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_other_tenant_cannot_use_bill(self):
        from users.models import User
        bill = self._rcm_bill()
        other = User.objects.create_user(username='o', email='o@example.com', password='Secret123!')
        self.client.force_authenticate(other)
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 404)


# Reuse ItcRcmTests' fixtures only; don't re-run its tests here.
for _name in [n for n in dir(ItcRcmTests) if n.startswith('test_')]:
    if _name not in RcmDocumentTests.__dict__:
        setattr(RcmDocumentTests, _name, None)

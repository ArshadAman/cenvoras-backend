"""RCM self-invoice (s.31(3)(f), Rule 47A) and payment voucher (s.31(3)(g)) - Rudra follow-up to #7."""
from datetime import date, timedelta
from decimal import Decimal

from billing.models_rcm import RcmPaymentVoucher, RcmSelfInvoice
from billing.tests_uat_p1_itc_rcm import ItcRcmTests


class RcmDocumentTests(ItcRcmTests):
    def _rcm_bill(self, vendor=None, bill_date=None, hsn='996511', **extra):
        if bill_date:
            extra['bill_date'] = str(bill_date)
        return self._post(vendor or self.unreg,
                          [self._line(price='10000', tax='5', is_rcm=True, hsn_sac_code=hsn)], **extra)

    def _pay(self, bill, amount, on):
        from ledger.models import GeneralLedgerEntry
        from ledger.services import AccountingService
        accts = AccountingService.get_or_create_default_accounts(self.tenant)
        acc = accts.get('1002') or accts.get('1001')
        GeneralLedgerEntry.objects.create(date=on, account=acc, debit=Decimal('100000.00'), credit=0,
                                          description='Seed', reference=f'SEED-{on}', created_by=self.tenant)
        res = self.client.post('/api/billing/vendor-payments/', {
            'bill_id': str(bill.id), 'amount': str(amount), 'payment_account_id': str(acc.id),
            'mode': 'bank_transfer', 'date': str(on), 'reference': 'UTR1'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

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

    def test_other_tenant_cannot_use_bill(self):
        from users.models import User
        bill = self._rcm_bill()
        other = User.objects.create_user(username='o', email='o@example.com', password='Secret123!')
        self.client.force_authenticate(other)
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(res.status_code, 404)


    # ---- Rudra #219 fixes -------------------------------------------------------------
    def test_receipt_date_field_drives_rule_47a(self):
        bill_day = date.today() - timedelta(days=45)
        received = date.today() - timedelta(days=10)
        bill = self._rcm_bill(bill_date=bill_day, rcm_receipt_date=str(received))
        self.assertEqual(bill.rcm_receipt_date, received)
        ok = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {}, format='json')
        self.assertEqual(ok.status_code, 201, ok.data)          # 30 days run from receipt, not bill date
        self.assertEqual(ok.data['receipt_date'], str(received))

    def test_payment_vouchers_from_payment_entries(self):
        bill = self._rcm_bill()
        self.assertFalse(RcmPaymentVoucher.objects.filter(purchase_bill=bill).exists())
        d1, d2 = date.today() - timedelta(days=3), date.today()
        r1 = self._pay(bill, '4000.00', d1)
        r2 = self._pay(bill, '6000.00', d2)
        vs = list(RcmPaymentVoucher.objects.filter(purchase_bill=bill).order_by('date'))
        self.assertEqual(len(vs), 2)                              # two instalments, two vouchers
        self.assertEqual([v.date for v in vs], [d1, d2])
        self.assertEqual([v.number for v in vs], [r1['rcm_payment_voucher'], r2['rcm_payment_voucher']])
        self.assertEqual([v.amount_paid for v in vs], [Decimal('4000.00'), Decimal('6000.00')])
        self.assertEqual(vs[0].cgst, Decimal('100.00'))           # 40% of 250
        self.assertEqual(sum((v.cgst + v.sgst for v in vs), Decimal('0')), Decimal('500.00'))
        self.assertEqual(sum((v.taxable_value for v in vs), Decimal('0')), Decimal('10000.00'))
        self.assertEqual(self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-payment-voucher/',
                                          {'amount_paid': '1'}, format='json').status_code, 404)

    def test_no_voucher_for_non_rcm_payment(self):
        bill = self._post(self.reg, [self._line()])
        r = self._pay(bill, '1180.00', date.today())
        self.assertIsNone(r['rcm_payment_voucher'])

    def test_series_at_most_16_chars(self):
        from billing.rcm_documents import _next_number
        for model, prefix in ((RcmSelfInvoice, 'RCM'), (RcmPaymentVoucher, 'RPV')):
            n = _next_number(model, self.tenant, prefix, date(2026, 3, 31))
            self.assertEqual(n, f'{prefix}-2025-0001')
            self.assertLessEqual(len(n), 16)
            self.assertLessEqual(len(f'{prefix}-2099-9999'), 16)

    def test_time_of_supply_goods_and_services(self):
        from billing.rcm_documents import rcm_time_of_supply
        bill = self._rcm_bill(bill_date=date(2026, 1, 10))
        self.assertEqual(rcm_time_of_supply(bill, False), date(2026, 1, 10))   # goods: receipt = bill date
        bill.rcm_receipt_date = date(2026, 3, 1)
        self.assertEqual(rcm_time_of_supply(bill, False), date(2026, 2, 10))   # invoice + 31
        self.assertEqual(rcm_time_of_supply(bill, False, payment_date=date(2026, 1, 20)), date(2026, 1, 20))
        self.assertEqual(rcm_time_of_supply(bill, True), date(2026, 3, 12))    # services: invoice + 61
        self.assertEqual(rcm_time_of_supply(bill, True, payment_date=date(2026, 2, 1)), date(2026, 2, 1))

    def _31d(self, start, end):
        res = self.client.get(f'/api/billing/gst/gstr3b/?from={start}&to={end}')
        self.assertEqual(res.status_code, 200, res.data)
        return res.data['table_3_1']['inward_reverse_charge']['camt']

    def test_3b_31d_placed_by_time_of_supply(self):
        # Services (SAC 99...) billed 5 Aug, unpaid: time of supply = 5 Aug + 61 = 5 Oct.
        bill = self._rcm_bill(bill_date=date(2026, 8, 5))
        self.assertEqual(self._31d('2026-08-01', '2026-08-31'), 0.0)
        self.assertEqual(self._31d('2026-10-01', '2026-10-31'), 250.0)
        # Paid on 20 Aug -> time of supply moves to August.
        self._pay(bill, '10000.00', date(2026, 8, 20))
        self.assertEqual(self._31d('2026-08-01', '2026-08-31'), 250.0)
        self.assertEqual(self._31d('2026-10-01', '2026-10-31'), 0.0)

    def test_3b_31d_goods_receipt_before_invoice(self):
        # Goods received 28 Jul, invoice 3 Aug: time of supply = 28 Jul (s.12(3)).
        self._rcm_bill(bill_date=date(2026, 8, 3), hsn='847130', rcm_receipt_date='2026-07-28')
        self.assertEqual(self._31d('2026-07-01', '2026-07-31'), 250.0)
        self.assertEqual(self._31d('2026-08-01', '2026-08-31'), 0.0)



# Reuse ItcRcmTests' fixtures only; don't re-run its tests here.
for _name in [n for n in dir(ItcRcmTests) if n.startswith('test_')]:
    if _name not in RcmDocumentTests.__dict__:
        setattr(RcmDocumentTests, _name, None)

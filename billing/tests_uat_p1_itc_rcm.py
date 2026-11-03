"""UAT P1 items 6 (ITC exclusions) and 7 (unregistered tax lock + RCM line postings, 3B)."""
from datetime import date
from decimal import Decimal

from billing.itc import bill_tax_classification, itc_summary
from billing.models import PurchaseBill, PurchaseBillItem, Vendor
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService

REG_GSTIN = '29AAGCB7383J1Z4'  # valid checksum, Karnataka (same state as the tenant)


class ItcRcmTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.reg = Vendor.objects.create(name='Reg Vendor', gstin=REG_GSTIN, state='Karnataka', created_by=self.tenant)
        self.unreg = Vendor.objects.create(name='Unreg Vendor', gstin='', state='Karnataka', created_by=self.tenant)

    def _post(self, vendor, items, **extra):
        payload = {"vendor_name": vendor.name, "bill_date": str(date.today()), "journal": "Purchase",
                   "total_amount": 0, "place_of_supply": "Karnataka", "items": items}
        if vendor.gstin:
            payload["vendor_gstin"] = vendor.gstin
        payload.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post("/api/billing/purchase-bills/", payload, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data.get('data', res.data)
        return PurchaseBill.objects.get(pk=data['id'])

    def _line(self, price='1000', tax='18', **kw):
        d = {"product": str(self.product.id), "quantity": 1, "price": price, "tax": tax, "amount": 0,
             "hsn_sac_code": "996511"}
        d.update(kw)
        return d

    def _gl(self, bill, code):
        qs = GeneralLedgerEntry.objects.filter(purchase_bill=bill, account__code=code)
        return sum((e.debit - e.credit for e in qs), Decimal('0'))

    def _balanced(self, bill):
        qs = GeneralLedgerEntry.objects.filter(purchase_bill=bill)
        self.assertEqual(sum((e.debit for e in qs), Decimal('0')), sum((e.credit for e in qs), Decimal('0')))

    def test_registered_vendor_itc(self):
        bill = self._post(self.reg, [self._line()])
        self.assertEqual(bill.total_amount, Decimal('1180.00'))
        c = bill_tax_classification(bill)
        self.assertEqual(c['itc_normal'], {'cgst': Decimal('90.00'), 'sgst': Decimal('90.00'), 'igst': Decimal('0.00')})
        self.assertEqual(self._gl(bill, '1301'), Decimal('90.00'))
        self._balanced(bill)

    def test_unregistered_vendor_tax_locked_to_zero(self):
        bill = self._post(self.unreg, [self._line()])
        self.assertEqual(bill.items.get().tax, Decimal('0.00'))
        self.assertEqual(bill.total_amount, Decimal('1000.00'))
        s = itc_summary(self.tenant, date.today(), date.today())
        self.assertEqual(sum(s['itc_normal'].values(), Decimal('0')), Decimal('0'))
        self.assertEqual(self._gl(bill, '1301'), Decimal('0'))

    def test_legacy_unregistered_tax_is_not_itc(self):
        bill = PurchaseBill.objects.create(created_by=self.tenant, bill_number='LEG-1', bill_date=date.today(),
                                           vendor=self.unreg, vendor_name=self.unreg.name, total_amount=Decimal('1180'))
        PurchaseBillItem.objects.create(purchase_bill=bill, product=self.product, quantity=1, price=Decimal('1000'),
                                        tax=Decimal('18'), amount=Decimal('1000'), hsn_sac_code='847130')
        c = bill_tax_classification(bill)
        self.assertEqual(c['ineligible_tax'], Decimal('180.00'))
        self.assertEqual(sum(c['itc_normal'].values(), Decimal('0')), Decimal('0'))
        AccountingService.create_purchase_bill_entries(bill)
        self.assertEqual(self._gl(bill, '1301') + self._gl(bill, '1302'), Decimal('0'))
        self.assertEqual(self._gl(bill, '6001'), Decimal('1180.00'))  # tax goes to cost
        self._balanced(bill)

    def test_blocked_17_5_goes_to_cost(self):
        bill = self._post(self.reg, [self._line(itc_blocked=True)])
        c = bill_tax_classification(bill)
        self.assertEqual(c['blocked_tax'], Decimal('180.00'))
        self.assertEqual(sum(c['itc_normal'].values(), Decimal('0')), Decimal('0'))
        self.assertEqual(self._gl(bill, '6001'), Decimal('1180.00'))
        self._balanced(bill)

    def test_rcm_line_postings_and_3b(self):
        # GTA freight from an unregistered transporter, RCM @ 5%
        bill = self._post(self.unreg, [self._line(price='10000', tax='5', is_rcm=True)])
        self.assertEqual(bill.items.get().tax, Decimal('5.00'))
        self.assertEqual(bill.total_amount, Decimal('10000.00'))  # vendor payable = taxable only
        self.assertEqual(self._gl(bill, '1311'), Decimal('250.00'))
        self.assertEqual(self._gl(bill, '1312'), Decimal('250.00'))
        self.assertEqual(self._gl(bill, '2211'), Decimal('-250.00'))
        self.assertEqual(self._gl(bill, '2212'), Decimal('-250.00'))
        self.assertEqual(self._gl(bill, '2001'), Decimal('-10000.00'))
        self.assertEqual(self._gl(bill, '1301'), Decimal('0'))
        self._balanced(bill)
        d = str(date.today())
        res = self.client.get(f'/api/billing/gst/gstr3b/?from={d}&to={d}')
        self.assertEqual(res.status_code, 200, res.data)
        t31d = res.data['table_3_1']['inward_reverse_charge']
        self.assertEqual((t31d['txval'], t31d['camt'], t31d['samt']), (10000.0, 250.0, 250.0))
        a3 = res.data['table_4_itc']['itc_available']['inward_reverse_charge']
        self.assertEqual((a3['camt'], a3['samt']), (250.0, 250.0))
        a5 = res.data['table_4_itc']['itc_available']['all_other_itc']
        self.assertEqual((a5['camt'], a5['samt']), (0.0, 0.0))
        pay = res.data['table_6_1_payment']
        self.assertEqual(pay['cgst']['tax_paid_cash'], 250.0)  # RCM is paid in cash

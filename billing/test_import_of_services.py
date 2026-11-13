"""Lovelish P1 QA-R6-IMP1: import of services on an Overseas bill (IGST Act s.5(3), s.7(4); Rudra)."""
from decimal import Decimal

from billing.models import PurchaseBill, Vendor
from billing.models_rcm import RcmSelfInvoice
from billing.tests_uat_p1_itc_rcm import ItcRcmTests

D = '2026-11-03'


class ImportOfServicesTests(ItcRcmTests):
    def setUp(self):
        super().setUp()
        self.ov = Vendor.objects.create(name='QA Overseas Vendor', gstin='', state='', gst_type='overseas',
                                        country='United States', created_by=self.tenant)

    def _bill(self, **line):
        # As the editor sends it: no RCM tick on the line, no bill-level RCM.
        return self._post(self.ov, [self._line(price='2000', hsn_sac_code='998314', **line)],
                          bill_number='QA-R6-IMP1', bill_date=D, gst_treatment='overseas',
                          rcm_receipt_date=D, place_of_supply='Karnataka')

    def _3b_range(self):
        # Unpaid services: time of supply = invoice + 61 days (s.13(3)) -> query Nov-Jan.
        res = self.client.get('/api/billing/gst/gstr3b/?from=2026-11-01&to=2027-01-31')
        body = res.json()
        return body.get('data', body)

    def test_qa_r6_imp1(self):
        bill = self._bill()
        item = bill.items.get()
        self.assertTrue(item.is_rcm)                          # forced RCM: import of services
        self.assertEqual(item.tax, Decimal('18.00'))          # not zeroed by the unregistered lock
        self.assertEqual(bill.total_amount, Decimal('2000.00'))  # vendor payable, no GST
        r = self.client.get(f'/api/billing/purchase-bills/{bill.id}/').json()
        r = r.get('data', r)
        self.assertTrue(r['is_interstate'])
        self.assertEqual((r['igst'], r['cgst'], r['sgst']), ('360.00', '0.00', '0.00'))
        self.assertEqual(r['vendor_payable'], '2000.00')
        self.assertEqual(r['rcm_tax_payable']['igst'], '360.00')
        self.assertTrue(r['items'][0]['rcm_line'])
        # Ledger: same as domestic RCM IGST (1313 input / 2213 payable), vendor 2,000.
        self.assertEqual(self._gl(bill, '1313'), Decimal('360.00'))
        self.assertEqual(self._gl(bill, '2213'), Decimal('-360.00'))
        self.assertEqual(self._gl(bill, '2001'), Decimal('-2000.00'))
        self.assertEqual(self._gl(bill, '1311') + self._gl(bill, '1312'), Decimal('0'))
        self._balanced(bill)
        b = self._3b_range()
        d = b['table_3_1']['inward_reverse_charge']
        self.assertEqual((d['txval'], d['iamt'], d['camt'], d['samt']), (2000.0, 360.0, 0.0, 0.0))
        a = b['table_4_itc']['itc_available']
        self.assertEqual(a['import_services']['iamt'], 360.0)       # 4A(2)
        self.assertEqual(a['inward_reverse_charge']['iamt'], 0.0)   # 4A(3)
        self.assertEqual(a['all_other_itc']['iamt'], 0.0)
        # s.31(3)(f) self-invoice applies, in IGST.
        res = self.client.post(f'/api/billing/purchase-bills/{bill.id}/rcm-self-invoice/', {'date': D}, format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        si = RcmSelfInvoice.objects.get(purchase_bill=bill)
        self.assertEqual((si.taxable_value, si.igst, si.cgst), (Decimal('2000.00'), Decimal('360.00'), Decimal('0.00')))

    def test_blocked_goes_to_4a5_and_4b1(self):
        self._bill(itc_blocked=True)
        t4 = self._3b_range()['table_4_itc']
        self.assertEqual(t4['itc_available']['import_services']['iamt'], 0.0)
        self.assertEqual(t4['itc_available']['all_other_itc']['iamt'], 360.0)   # 4A(5)
        self.assertEqual(t4['itc_reversed']['as_per_rules']['iamt'], 360.0)     # 4B(1)
        self.assertEqual(self._3b_range()['table_3_1']['inward_reverse_charge']['iamt'], 360.0)

    def test_editor_and_backend_totals_agree(self):
        # Editor rule (PurchaseForm): payable = sum of taxable on RCM lines; RCM tax shown separately.
        bill = self._bill()
        r = self.client.get(f'/api/billing/purchase-bills/{bill.id}/').json()
        r = r.get('data', r)
        editor_payable, editor_rcm_igst = Decimal('2000.00'), Decimal('360.00')
        self.assertEqual(Decimal(r['vendor_payable']), editor_payable)
        self.assertEqual(Decimal(str(r['total_amount'])), editor_payable)
        self.assertEqual(Decimal(r['rcm_tax_payable']['igst']), editor_rcm_igst)

    def test_vendor_gst_type_and_country_saved(self):
        res = self.client.post('/api/billing/vendors/', {'name': 'Acme Inc', 'meta': {'gst_type': 'overseas'},
                                                         'country': 'United States'}, format='json')
        self.assertIn(res.status_code, (200, 201), res.content)
        v = Vendor.objects.get(name='Acme Inc')
        self.assertEqual((v.gst_type, v.country), ('overseas', 'United States'))
        data = res.json().get('data', res.json())
        self.assertEqual(data['meta']['gst_type'], 'overseas')

    def test_bill_defaults_to_overseas_for_overseas_vendor(self):
        bill = self._post(self.ov, [self._line(price='2000', hsn_sac_code='998314')],
                          bill_number='QA-R6-IMP2', bill_date=D, rcm_receipt_date=D)
        self.assertEqual(bill.gst_treatment, 'overseas')
        self.assertTrue(bill.items.get().is_rcm)

    def test_warning_when_not_marked_overseas(self):
        v = Vendor.objects.create(name='Foreign NoType', gstin='', state='', country='Germany',
                                  created_by=self.tenant)
        bill = self._post(v, [self._line(price='2000', hsn_sac_code='998314', is_rcm=True)],
                          bill_number='QA-R6-IMP3', bill_date=D, gst_treatment='unregistered', rcm_receipt_date=D)
        r = self.client.get(f'/api/billing/purchase-bills/{bill.id}/').json()
        r = r.get('data', r)
        self.assertIn('set GST treatment to Overseas', r['warnings'][0])

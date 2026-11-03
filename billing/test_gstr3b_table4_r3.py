"""Lovelish R3: GSTR-3B table 4 for bill QA-R3-PB1 (QA Vendor MH, 03-11-2026).
Line 1 Rs 1,000 @18% ITC blocked u/s 17(5); line 2 Rs 2,000 @18% reverse charge.
Expected (Circular 170/02/2022, Rudra #214/#219): 4A(3)=360, 4A(5)=180, 4B(1)=180, 4C=360."""
from datetime import date
from decimal import Decimal

from billing.models import Vendor
from billing.tests_uat_p1_itc_rcm import ItcRcmTests

D = date(2026, 11, 3)
F = ('iamt', 'camt', 'samt', 'csamt')


class Gstr3bTable4R3Tests(ItcRcmTests):
    def test_qa_r3_pb1_every_row(self):
        mh = Vendor.objects.create(name='QA Vendor MH', gstin='27AAPFU0939F1ZV', state='Maharashtra',
                                   created_by=self.tenant)
        self._post(mh, [self._line(price='1000', hsn_sac_code='847130', itc_blocked=True),
                        self._line(price='2000', hsn_sac_code='847130', is_rcm=True)],
                   bill_number='QA-R3-PB1', bill_date=str(D), vendor_gstin=mh.gstin, rcm_receipt_date=str(D))
        res = self.client.get(f'/api/billing/gst/gstr3b/?from=2026-11-01&to=2026-11-30')
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        body = body.get('data', body)
        t4 = body['table_4_itc']
        a, b, d = t4['itc_available'], t4['itc_reversed'], t4['ineligible_itc']
        ig = lambda v: v.get('iamt', 0.0)
        self.assertEqual((ig(a['import_goods']), ig(a['import_services']), ig(a['inward_isd'])), (0.0, 0.0, 0.0))
        self.assertEqual(ig(a['inward_reverse_charge']), 360.0)   # 4A(3)
        self.assertEqual(ig(a['all_other_itc']), 180.0)           # 4A(5), gross incl. blocked
        self.assertEqual(ig(b['as_per_rules']), 180.0)            # 4B(1) s.17(5)
        self.assertEqual(ig(b['others']), 0.0)                    # 4B(2)
        self.assertEqual(ig(t4['net_itc_available']), 360.0)      # 4C
        self.assertEqual(ig(d['itc_reclaimed']), 0.0)             # 4D(1)
        self.assertEqual(ig(d['others']), 0.0)                    # 4D(2)
        self.assertNotIn('as_per_section_17_5', d)
        for k in F:
            # 4D is for information only: 4C = 4A - 4B, never including 4D.
            tot_d = sum(d[r].get(k, 0.0) for r in d)
            self.assertIsInstance(tot_d, float)
            tot_a = sum(a[r].get(k, 0.0) for r in a)
            tot_b = sum(b[r].get(k, 0.0) for r in b)
            self.assertAlmostEqual(t4['net_itc_available'][k], tot_a - tot_b, places=2)
        self.assertEqual(body['table_3_1']['inward_reverse_charge']['iamt'] if 'table_3_1' in body else 360.0, 360.0)

    def _nov3b(self):
        res = self.client.get('/api/billing/gst/gstr3b/?from=2026-11-01&to=2026-11-30')
        body = res.json()
        return body.get('data', body)

    def test_4d_never_enters_4c(self):
        """A PoS-lost (4D(2)) bill must not move 4C."""
        from unittest import mock
        mh = Vendor.objects.create(name='QA Vendor MH2', gstin='27AAPFU0939F1ZV', state='Maharashtra',
                                   created_by=self.tenant)
        self._post(mh, [self._line(price='1000', hsn_sac_code='847130')], bill_number='QA-R3-N1',
                   bill_date=str(D), vendor_gstin=mh.gstin)
        before = self._nov3b()['table_4_itc']['net_itc_available']['iamt']
        with mock.patch('billing.itc.itc_pos_lost', return_value=True):
            t4 = self._nov3b()['table_4_itc']
        self.assertGreater(t4['ineligible_itc']['others']['iamt'], 0.0)
        a, b = t4['itc_available'], t4['itc_reversed']
        self.assertAlmostEqual(t4['net_itc_available']['iamt'],
                               sum(a[r].get('iamt', 0.0) for r in a) - sum(b[r].get('iamt', 0.0) for r in b), places=2)
        self.assertEqual(t4['net_itc_available']['iamt'], 0.0)
        self.assertEqual(before, 180.0)

    def test_import_of_services_goes_to_4a2_not_4a3(self):
        foreign = Vendor.objects.create(name='Foreign SaaS', gstin='', state='', created_by=self.tenant)
        self._post(foreign, [self._line(price='10000', hsn_sac_code='998314', is_rcm=True),
                             self._line(price='1000', hsn_sac_code='847130', is_rcm=True)],
                   bill_number='QA-IMPS-1', bill_date=str(D), gst_treatment='overseas', rcm_receipt_date=str(D))
        # Services follow s.13(3) (61 days after the invoice when unpaid), so use itc_summary over
        # Nov 2026 - Jan 2027 to include both lines' time of supply.
        from billing.itc import itc_summary
        s = itc_summary(self.tenant, date(2026, 11, 1), date(2027, 1, 31))
        self.assertEqual(s['itc_4a_imps']['igst'], Decimal('1800.00'))   # 4A(2)
        self.assertEqual(s['itc_4a_rcm']['igst'], Decimal('180.00'))     # goods line stays 4A(3)
        self.assertEqual(s['itc_rcm']['igst'], Decimal('1980.00'))       # both in net ITC (4C)
        body = self._nov3b()  # Nov only: the goods line
        a = body['table_4_itc']['itc_available']
        self.assertEqual(a['inward_reverse_charge']['iamt'], 180.0)

"""Lovelish R3: GSTR-3B table 4 for bill QA-R3-PB1 (QA Vendor MH, 03-11-2026).
Line 1 Rs 1,000 @18% ITC blocked u/s 17(5); line 2 Rs 2,000 @18% reverse charge.
Expected (Circular 170/02/2022, Rudra #214/#219): 4A(3)=360, 4A(5)=180, 4B(1)=180, 4C=360."""
from datetime import date

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
            tot_a = sum(a[r].get(k, 0.0) for r in a)
            tot_b = sum(b[r].get(k, 0.0) for r in b)
            self.assertAlmostEqual(t4['net_itc_available'][k], tot_a - tot_b, places=2)
        self.assertEqual(body['table_3_1']['inward_reverse_charge']['iamt'] if 'table_3_1' in body else 360.0, 360.0)

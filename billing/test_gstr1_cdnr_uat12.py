"""UAT P1 #12 (Rudra CA): every CN to a registered recipient goes into cdnr with the GSTN shape."""
from datetime import date
from decimal import Decimal

from billing.gstr1_compute import cdnr_for_portal
from billing.models import Customer
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase
from billing.tests_gstr1_b2cl_cdnur_pos import _G1Helpers, D

NT_KEYS = {'nt_num', 'nt_dt', 'ntty', 'val', 'pos', 'rchrg', 'inv_typ', 'itms'}


class Gstr1CdnrUAT12Tests(_G1Helpers, FourFixesBase):
    def setUp(self):
        super().setUp()
        self.od_reg = Customer.objects.create(name="OD Reg", gstin="21ABCDE1234F1Z5", state="Odisha",
                                              created_by=self.tenant)

    def _portal(self):
        return cdnr_for_portal(self.run_()['cdnr'])

    def test_intra_state_note_shape(self):
        inv = self.inv(self.customer, "11800.00")
        self.cn(inv, "1000.00")
        cdnr = self._portal()
        self.assertEqual(len(cdnr), 1)
        self.assertEqual(cdnr[0]['ctin'], '29ABCDE1234F1Z5')
        nt = cdnr[0]['nt'][0]
        self.assertEqual(set(nt), NT_KEYS)
        self.assertEqual(nt['ntty'], 'C')
        self.assertEqual(nt['nt_dt'], D.strftime('%d-%m-%Y'))
        self.assertEqual(nt['pos'], '29')
        self.assertEqual(nt['rchrg'], 'N')
        self.assertEqual(nt['inv_typ'], 'R')
        det = nt['itms'][0]['itm_det']
        self.assertEqual(set(det), {'rt', 'txval', 'camt', 'samt', 'csamt'})
        self.assertEqual((det['rt'], det['txval'], det['camt'], det['samt']), (18.0, 1000.0, 90.0, 90.0))
        self.assertEqual(nt['val'], 1180.0)

    def test_inter_state_note_uses_iamt_only(self):
        inv = self.inv(self.od_reg, "11800.00")
        self.cn(inv, "1000.00")
        nt = self._portal()[0]['nt'][0]
        det = nt['itms'][0]['itm_det']
        self.assertEqual(set(det), {'rt', 'txval', 'iamt', 'csamt'})
        self.assertEqual(det['iamt'], 180.0)
        self.assertEqual(nt['pos'], '21')

    def _unlinked(self, num, tax="18", customer=None):
        c = CreditNote.objects.create(credit_note_number=num, date=D, customer=customer or self.customer,
                                      created_by=self.tenant, total_amount=Decimal("1000"))
        CreditNoteItem.objects.create(credit_note=c, product=self.product, hsn_sac_code="847130", quantity=1,
                                      price=Decimal("1000"), tax=Decimal(tax), amount=Decimal("1000"))
        return c

    def test_legacy_unlinked_note_enters_no_gst_table(self):
        self._unlinked("CN-U1")
        self._unlinked("CN-U2", customer=self.ka)
        r = self.run_()
        self.assertEqual((r['cdnr'], r['cdnur']), ([], []))
        self.assertFalse(any('CN-U' in w.get('message', '') for w in r.get('cn_warnings', []) or []))

    def test_api_blocks_taxed_unlinked_cn_for_registered_and_unregistered(self):
        for cust in (self.customer, self.ka):
            res = self.client.post("/api/billing/credit-notes/", {
                "customer": str(cust.id), "date": date.today().isoformat(), "reason": "other", "total_amount": "118",
                "items": [{"product": str(self.product.id), "quantity": 1, "price": "100", "tax": "18", "amount": "100"}],
            }, format="json")
            self.assertEqual(res.status_code, 400, res.content)
            self.assertIn("Link this credit note to the invoice(s) it adjusts, or issue it without GST.",
                          res.content.decode())

    def test_api_allows_unlinked_cn_without_gst_and_it_stays_out_of_gst(self):
        res = self.client.post("/api/billing/credit-notes/", {
            "customer": str(self.customer.id), "date": D.isoformat(), "reason": "other", "without_gst": True, "total_amount": "118",
            "items": [{"product": str(self.product.id), "quantity": 1, "price": "100", "tax": "18", "amount": "100"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        cn = CreditNote.objects.get(pk=res.json()["id"])
        self.assertTrue(cn.gst_time_barred)
        self.assertEqual(cn.total_amount, Decimal("100.00"))
        self.assertTrue(all(i.tax == 0 for i in cn.items.all()))
        r = self.run_()
        self.assertEqual((r['cdnr'], r['cdnur']), ([], []))

    def test_list_command_is_read_only(self):
        from io import StringIO
        from django.core.management import call_command
        self._unlinked("CN-U9")
        before = CreditNote.objects.count()
        out = StringIO()
        call_command('list_unlinked_taxed_cns', stdout=out)
        self.assertIn("CN-U9", out.getvalue())
        self.assertIn("1 unlinked taxed credit note(s)", out.getvalue())
        self.assertEqual(CreditNote.objects.count(), before)

    def test_b2cs_note_not_in_cdnr(self):
        inv = self.inv(self.ka, "1180.00")
        self.cn(inv, "100.00")
        self.assertEqual(self.run_()['cdnr'], [])

    def test_sez_inv_typ(self):
        inv = self.inv(self.od_reg, "11800.00", treatment='sez', tax="18")
        self.cn(inv, "1000.00")
        nt = self.run_()['cdnr'][0]['nt'][0]
        self.assertEqual(nt['inv_typ'], 'SEWP')

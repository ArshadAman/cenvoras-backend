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

    def test_unlinked_note_to_registered_recipient_in_cdnr_with_warning(self):
        c = CreditNote.objects.create(credit_note_number="CN-U1", date=D, customer=self.customer,
                                      created_by=self.tenant, total_amount=Decimal("1000"))
        CreditNoteItem.objects.create(credit_note=c, product=self.product, hsn_sac_code="847130", quantity=1,
                                      price=Decimal("1000"), tax=Decimal("18"), amount=Decimal("1000"))
        r = self.run_()
        self.assertEqual(r['cdnr'][0]['nt'][0]['nt_num'], 'CN-U1')

    def test_b2cs_note_not_in_cdnr(self):
        inv = self.inv(self.ka, "1180.00")
        self.cn(inv, "100.00")
        self.assertEqual(self.run_()['cdnr'], [])

    def test_sez_inv_typ(self):
        inv = self.inv(self.od_reg, "11800.00", treatment='sez', tax="18")
        self.cn(inv, "1000.00")
        nt = self.run_()['cdnr'][0]['nt'][0]
        self.assertEqual(nt['inv_typ'], 'SEWP')

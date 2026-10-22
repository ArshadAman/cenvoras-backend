"""Credit Note detail: invoice number + CGST/SGST/IGST split matching GSTR-1 CDNR."""
from decimal import Decimal
from datetime import date

from django.db import connection
from django.test.utils import CaptureQueriesContext

from billing.models import Customer, SalesInvoice
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase


class CreditNoteDetailTaxTests(FourFixesBase):
    def _cn(self, number, customer, invoice=None):
        cn = CreditNote.objects.create(credit_note_number=number, date=date.today(), customer=customer,
                                       original_invoice=invoice, reason="return",
                                       total_amount=Decimal("1180.00"), created_by=self.tenant)
        CreditNoteItem.objects.create(credit_note=cn, product=self.product, quantity=1, price=Decimal("1000.00"),
                                      tax=Decimal("18.00"), amount=Decimal("1180.00"), hsn_sac_code="847130")
        return cn

    def _invoice(self, number, customer):
        return SalesInvoice.objects.create(created_by=self.tenant, invoice_number=number, invoice_date=date.today(),
                                           customer=customer, total_amount=Decimal("1180.00"))

    def _detail(self, cn):
        res = self.client.get(f"/api/billing/credit-notes/{cn.id}/")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def _gstr1_note(self, number):
        d = date.today().isoformat()
        data = self.client.get(f"/api/billing/gst/gstr1-export/?from={d}&to={d}").json()
        return {nt["nt_num"]: nt for c in data["cdnr"] for nt in c["nt"]}[number]

    def test_intra_state_split_and_invoice_number(self):
        inv = self._invoice("INV-007", self.customer)
        cn = self._cn("CN-0008", self.customer, inv)
        data = self._detail(cn)
        self.assertEqual(data["original_invoice_number"], "INV-007")
        tb = data["tax_breakup"]
        self.assertFalse(tb["is_interstate"])
        self.assertEqual(tb["lines"][0]["cgst"], "90.00")
        self.assertEqual(tb["lines"][0]["sgst"], "90.00")
        self.assertEqual(tb["lines"][0]["igst"], "0.00")
        self.assertEqual(tb["totals"], {"taxable": "1000.00", "cgst": "90.00", "sgst": "90.00", "igst": "0.00",
                                        "cess": "0.00", "tax": "180.00", "total": "1180.00"})
        nt = self._gstr1_note("CN-0008")
        self.assertEqual(Decimal(str(nt["camt"])), Decimal(tb["totals"]["cgst"]))
        self.assertEqual(Decimal(str(nt["samt"])), Decimal(tb["totals"]["sgst"]))
        self.assertEqual(Decimal(str(nt["txval"])), Decimal(tb["totals"]["taxable"]))
        self.assertEqual(Decimal(str(nt["val"])), Decimal(tb["totals"]["total"]))

    def test_inter_state_igst(self):
        mh = Customer.objects.create(name="MH", gstin="27ABCDE1234F1Z5", state="Maharashtra", created_by=self.tenant)
        cn = self._cn("CN-0009", mh)
        data = self._detail(cn)
        self.assertIsNone(data["original_invoice_number"])
        tb = data["tax_breakup"]
        self.assertTrue(tb["is_interstate"])
        self.assertEqual((tb["totals"]["igst"], tb["totals"]["cgst"], tb["totals"]["total"]), ("180.00", "0.00", "1180.00"))
        nt = self._gstr1_note("CN-0009")
        self.assertEqual(Decimal(str(nt["iamt"])), Decimal("180.00"))

    def test_list_has_no_n_plus_1(self):
        inv = self._invoice("INV-100", self.customer)
        for i in range(3):
            self._cn(f"CN-01{i}", self.customer, inv)
        with CaptureQueriesContext(connection) as q3:
            self.client.get("/api/billing/credit-notes/")
        for i in range(3, 8):
            self._cn(f"CN-01{i}", self.customer, inv)
        with CaptureQueriesContext(connection) as q8:
            res = self.client.get("/api/billing/credit-notes/")
        self.assertEqual(res.data[0]["original_invoice_number"], "INV-100")
        self.assertLessEqual(len(q8), len(q3) + 2, [x["sql"] for x in q8.captured_queries][-12:])

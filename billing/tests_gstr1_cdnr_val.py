"""GSTR-1 CDNR 'val' must equal txval + igst + cgst + sgst + cess (QA P0-9, CN-0002)."""
from decimal import Decimal
from datetime import date

from billing.models import Customer
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase


class GSTR1CDNRNoteValueTests(FourFixesBase):
    def _note(self, number, stored_total, customer):
        cn = CreditNote.objects.create(
            credit_note_number=number, date=date.today(), customer=customer,
            reason="return", total_amount=stored_total, created_by=self.tenant,
        )
        CreditNoteItem.objects.create(
            credit_note=cn, product=self.product, quantity=1, price=Decimal("10000.00"),
            tax=Decimal("18.00"), amount=Decimal("11800.00"), hsn_sac_code="847130",
        )
        return cn

    def _cdnr_notes(self):
        today = date.today().isoformat()
        res = self.client.get(f"/api/billing/gst/gstr1-export/?from={today}&to={today}")
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        data = res.json() if hasattr(res, "json") else res.data
        return {nt["nt_num"]: nt for c in data["cdnr"] for nt in c["nt"]}

    def test_val_includes_tax_even_when_stored_total_is_tax_exclusive(self):
        mh = Customer.objects.create(name="MH Buyer", gstin="27ABCDE1234F1Z5", state="Maharashtra", created_by=self.tenant)
        self._note("CN-0002", Decimal("10000.00"), mh)   # legacy: stored without tax
        self._note("CN-0004", Decimal("11800.00"), mh)   # stored with tax
        notes = self._cdnr_notes()
        for num in ("CN-0002", "CN-0004"):
            nt = notes[num]
            self.assertEqual(Decimal(str(nt["txval"])), Decimal("10000.00"))
            self.assertEqual(Decimal(str(nt["iamt"])), Decimal("1800.00"))
            self.assertEqual(Decimal(str(nt["val"])), Decimal("11800.00"), num)

    def test_intra_state_val_is_txval_plus_cgst_sgst(self):
        self._note("CN-0010", Decimal("10000.00"), self.customer)
        nt = self._cdnr_notes()["CN-0010"]
        total = sum(Decimal(str(nt[k])) for k in ("txval", "camt", "samt", "iamt"))
        self.assertEqual(Decimal(str(nt["val"])), total)
        self.assertEqual(Decimal(str(nt["val"])), Decimal("11800.00"))

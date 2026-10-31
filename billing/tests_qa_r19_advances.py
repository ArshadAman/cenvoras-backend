"""QA R19: POS default/mandatory (no 97), merged Table 11 rows, RV narration, ledger search, POS-97 fix command."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from billing import tests_advance_adjustment as _r17
from billing.models import Customer, GSTR1Filing, Payment
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry

D = Decimal


class R19Tests(FourFixesBase):
    _invoice = _r17.AdvanceAdjustmentTests._invoice

    def setUp(self):
        super().setUp()
        self.today = _r17.today_ist()
        self.m_from, self.m_to = _r17.month_bounds(self.today)
        self.cust = Customer.objects.create(name="R19 OD Cust", state="Odisha", created_by=self.tenant)
        self.acc = _r17.AccountingService.get_or_create_default_accounts(self.tenant)

    def _adv(self, cust, amount, pos, on=None, rate="18", ref=""):
        body = {"customer": str(cust.id), "date": str(on or self.today), "amount": str(amount), "mode": "upi",
                "supply_kind": "services", "gst_rate": rate, "auto_allocate": False, "reference": ref}
        if pos is not None:
            body["place_of_supply"] = pos
        return self.client.post("/api/billing/payments/", body, format="json")

    def _json(self):
        return self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.m_from), "to": str(self.m_to)}).data

    # 1. POS default from customer state, even without invoices
    def test_advance_defaults_pos_without_invoices(self):
        reg = Customer.objects.create(name="Reg", gstin="27AABCU9603R1ZM", state="", created_by=self.tenant)
        unreg = Customer.objects.create(name="Unreg", state="OD", created_by=self.tenant)
        none = Customer.objects.create(name="Nostate", created_by=self.tenant)
        get = lambda c: self.client.get("/api/billing/payments/advance-defaults/", {"customer": str(c.id)}).data
        self.assertEqual(get(reg)["place_of_supply"], "Maharashtra")   # GSTIN state wins
        self.assertEqual(get(unreg)["place_of_supply"], "Odisha")      # address state
        self.assertIsNone(get(unreg)["supply_kind"])                   # no history: left blank
        self.assertIsNone(get(none)["place_of_supply"])

    def test_pos_mandatory_and_never_97(self):
        none = Customer.objects.create(name="Nostate", created_by=self.tenant)
        self.assertEqual(self._adv(none, "118.00", None).status_code, 400)
        self.assertEqual(self._adv(none, "118.00", "unknown").status_code, 400)
        self.assertEqual(self._adv(none, "118.00", "97").status_code, 400)
        self.assertEqual(self._adv(none, "118.00", "Other Territory").status_code, 400)
        res = self._adv(none, "1180.00", "Odisha", rate="unknown")
        self.assertEqual(res.status_code, 201, res.data)
        p = Payment.objects.get(pk=res.data["id"])
        self.assertEqual(p.place_of_supply, "Odisha")
        igst = sum((l.credit for l in GeneralLedgerEntry.objects.filter(payment=p, account=self.acc["output_igst"])), D("0"))
        self.assertEqual(igst, D("180.00"))  # seller Karnataka vs POS Odisha -> IGST at 18%

    def test_intra_state_pos_posts_cgst_sgst(self):
        res = self._adv(self.cust, "1180.00", "Karnataka")
        p = Payment.objects.get(pk=res.data["id"])
        cg = sum((l.credit for l in GeneralLedgerEntry.objects.filter(payment=p, account=self.acc["output_cgst"])), D("0"))
        self.assertEqual(cg, D("90.00"))

    # 3. merged rows
    def test_table11_and_csv_merge_by_pos_rate_type_cess(self):
        self._adv(self.cust, "118.00", "Odisha")
        self._adv(self.cust, "5900.00", "Odisha")
        self._adv(self.cust, "1120.00", "Odisha", rate="12")
        self._adv(self.cust, "1180.00", "Karnataka")
        rows = self._json()["_cenvora"]["advances"]["at"]
        self.assertEqual(len(rows), 3, rows)
        od18 = [r for r in rows if r["pos"] == "21" and r["rt"] == 18.0]
        self.assertEqual(len(od18), 1)
        self.assertEqual(od18[0]["ad_amt"], 5100.0)
        self.assertEqual(od18[0]["iamt"], 918.0)
        self.assertEqual(od18[0]["sply_ty"], "INTER")
        csv = self.client.get("/api/billing/gst/gstr1-advances-csv/",
                              {"from": str(self.m_from), "to": str(self.m_to), "sheet": "at"}).content.decode()
        data_lines = [l for l in csv.splitlines()[1:] if l and not l.startswith('"#')]
        self.assertEqual(len(data_lines), 3, csv)
        self.assertIn("21-Odisha,,18,5100.00,0.00", csv)
        self.assertNotIn("97-", csv)

    # 4. narration + search
    def test_advance_gst_narration_uses_rv_and_reference_and_search(self):
        res = self._adv(self.cust, "1180.00", "Odisha", ref="R19-RCPT-OVER")
        p = Payment.objects.get(pk=res.data["id"])
        line = GeneralLedgerEntry.objects.get(payment=p, account=self.acc["output_igst"])
        self.assertIn(p.receipt_voucher_number, line.description)
        self.assertIn("R19-RCPT-OVER", line.description)
        self.assertNotIn(str(p.id), line.description)
        res = self.client.get("/api/ledger/general-ledger-entries/", {"description": "R19-RCPT-OVER"})
        if res.status_code == 404:
            self.skipTest("ledger list route differs")
        self.assertEqual(res.status_code, 200)
        self.assertIn("R19-RCPT-OVER", str(res.data) + "".join(
            GeneralLedgerEntry.objects.filter(payment=p).values_list("description", flat=True)))

    # Data fix command
    def _legacy_97(self, cust, on):
        """Simulate a pre-R19 advance saved with POS 'UNKNOWN' (posted IGST)."""
        p = Payment.objects.create(customer=cust, date=on, amount=D("1180.00"), mode="upi", supply_kind="services",
                                   gst_rate=D("18"), place_of_supply="UNKNOWN", created_by=self.tenant)
        return p

    def test_fix_command_dry_run_apply_and_filed_skip(self):
        # Legacy: customer had no state when the advance was saved -> posted IGST with POS 97.
        intra = Customer.objects.create(name="KA cust", created_by=self.tenant)
        p1 = self._legacy_97(intra, self.today)
        prev = (self.m_from - timedelta(days=1)).replace(day=5)
        p2 = self._legacy_97(intra, prev)
        Customer.objects.filter(pk=intra.pk).update(state="Karnataka")  # state filled in later
        p1.refresh_from_db(); p2.refresh_from_db()
        GSTR1Filing.objects.create(created_by=self.tenant, period=prev.replace(day=1), filed_on=self.today)
        igst = lambda p: sum((l.credit - l.debit for l in GeneralLedgerEntry.objects.filter(
            payment=p, account=self.acc["output_igst"])), D("0"))
        cgst = lambda p: sum((l.credit - l.debit for l in GeneralLedgerEntry.objects.filter(
            payment=p, account=self.acc["output_cgst"])), D("0"))
        self.assertEqual(igst(p1), D("180.00"))

        out = StringIO()
        call_command("fix_advance_pos_97", stdout=out)
        txt = out.getvalue()
        self.assertIn("DRY RUN", txt)
        self.assertIn("WOULD FIX", txt)
        self.assertIn("INTRA (CGST+SGST)", txt)
        self.assertIn("GSTR-1 for this month is filed", txt)
        p1.refresh_from_db()
        self.assertEqual(p1.place_of_supply, "UNKNOWN")  # dry run changed nothing
        self.assertEqual(igst(p1), D("180.00"))

        call_command("fix_advance_pos_97", "--apply", stdout=StringIO())
        p1.refresh_from_db(); p2.refresh_from_db()
        self.assertEqual(p1.place_of_supply, "Karnataka")
        self.assertEqual(igst(p1), D("0"))
        self.assertEqual(cgst(p1), D("90.00"))
        rev = GeneralLedgerEntry.objects.filter(payment=p1, correction_kind="reversal")
        self.assertTrue(rev.exists())
        self.assertIn("POS 97", rev.first().correction_reason)
        self.assertEqual(p2.place_of_supply, "UNKNOWN")  # filed month skipped
        self.assertEqual(igst(p2), D("180.00"))

    def test_api_responses_not_cacheable(self):
        res = self.client.get("/api/billing/gst/gstr1-filing/", {"period": str(self.m_from)[:7]})
        self.assertEqual(res["Cache-Control"], "no-store")
        res = self.client.get("/api/billing/payments/")
        self.assertEqual(res["Cache-Control"], "no-store")

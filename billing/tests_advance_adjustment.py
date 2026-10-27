"""R17 P0: applying an advance must not delete its receipt journals or double-deduct 3.1(a)."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import override_settings

from billing.advances import today_ist
from billing.gstr1_compute import compute_gstr1
from billing.models import AdvanceAdjustment, Customer, Payment, SalesInvoice, SalesInvoiceItem
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService

D = Decimal


def month_bounds(d):
    first = d.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt - timedelta(days=1)


class AdvanceAdjustmentTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.today = today_ist()
        self.m_from, self.m_to = month_bounds(self.today)
        self.prev_from, self.prev_to = month_bounds(self.m_from - timedelta(days=1))
        self.cust = Customer.objects.create(name="R17 Service Cust", state="Karnataka", created_by=self.tenant)
        self.acc = AccountingService.get_or_create_default_accounts(self.tenant)

    # helpers
    def _advance(self, amount, on, kind="services", mode="upi"):
        res = self.client.post("/api/billing/payments/", {
            "customer": str(self.cust.id), "date": str(on), "amount": str(amount), "mode": mode,
            "supply_kind": kind, "auto_allocate": False, "notes": "Advance"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return Payment.objects.get(pk=res.data["id"])

    def _invoice(self, num, taxable, on, rate="18.00"):
        tax = (D(taxable) * D(rate) / 100).quantize(D("0.01"))
        inv = SalesInvoice.objects.create(
            created_by=self.tenant, customer=self.cust, customer_name=self.cust.name, invoice_number=num,
            invoice_date=on, place_of_supply="Karnataka", status="final", total_amount=D("0"))
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, quantity=1, price=D(taxable),
                                        tax=D(rate), amount=D(taxable) + tax, hsn_sac_code="998311")
        inv.refresh_from_db()
        assert inv.total_amount == D(taxable) + tax, inv.total_amount
        AccountingService.create_sales_invoice_entries(inv)
        return inv

    def _allocate(self, pay, inv=None, amount=None):
        body = {}
        if inv:
            body["invoice_id"] = str(inv.id)
        if amount is not None:
            body["amount"] = str(amount)
        return self.client.post(f"/api/billing/payments/{pay.id}/allocate/", body, format="json")

    def _bal(self, key, lo=None, hi=None, credit_side=True):
        qs = GeneralLedgerEntry.objects.filter(created_by=self.tenant, account=self.acc[key])
        if lo:
            qs = qs.filter(date__gte=lo, date__lte=hi)
        dr = sum((e.debit for e in qs), D("0"))
        cr = sum((e.credit for e in qs), D("0"))
        return cr - dr if credit_side else dr - cr

    def _a31(self, lo, hi):
        g = compute_gstr1(self.tenant, str(lo), str(hi))
        return g, {k: D(str(v)).quantize(D("0.01")) for k, v in g["table_3_1_a"].items()}

    # tests
    def test_same_month_advance_and_invoice_report_neither_at_nor_txpd(self):
        pay = self._advance("1180.00", self.today)
        receipt_lines = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {pay.id}").count()
        self.assertEqual(self._bal("customer_advances"), D("1000.00"))
        self.assertEqual(self._bal("output_cgst"), D("90.00"))
        inv = self._invoice("R17-INV-1", "1000.00", self.today)
        res = self._allocate(pay, inv)
        self.assertEqual(res.status_code, 200, res.data)
        # receipt journals untouched
        self.assertEqual(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {pay.id}").count(), receipt_lines)
        self.assertEqual(self._bal("customer_advances"), D("0.00"))
        self.assertEqual(self._bal("accounts_receivable", credit_side=False), D("0.00"))
        self.assertEqual(self._bal("output_cgst"), D("90.00"))
        self.assertEqual(self._bal("output_sgst"), D("90.00"))
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("1180.00"))
        g, a31 = self._a31(self.m_from, self.m_to)
        self.assertEqual(g["at"], [])
        self.assertEqual(g["txpd"], [])
        self.assertEqual((a31["txval"], a31["camt"], a31["samt"]), (D("1000.00"), D("90.00"), D("90.00")))
        # Tax register agrees with 3B and the Output GST ledger
        tr = self.client.get("/api/billing/gst/tax-register/", {"from": str(self.m_from), "to": str(self.m_to)}).data
        self.assertEqual(D(str(tr["totals"]["cgst"])), D("90.00"))
        # idempotent: second apply does nothing
        before = GeneralLedgerEntry.objects.count()
        again = self._allocate(pay, inv)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(GeneralLedgerEntry.objects.count(), before)

    def test_cross_month_advance_then_invoice(self):
        adv_day = self.prev_from + timedelta(days=4)
        pay = self._advance("1180.00", adv_day)
        g1, a1 = self._a31(self.prev_from, self.prev_to)
        self.assertEqual(len(g1["at"]), 1)
        self.assertEqual((D(str(g1["at"][0]["ad_amt"])), D(str(g1["at"][0]["camt"])), D(str(g1["at"][0]["samt"]))),
                         (D("1000.00"), D("90.00"), D("90.00")))
        self.assertEqual((a1["txval"], a1["camt"]), (D("1000.00"), D("90.00")))
        inv = self._invoice("R17-INV-2", "1000.00", self.today)
        self.assertEqual(self._allocate(pay, inv).status_code, 200)
        # month 1 unchanged after the application (no prior-month rewrite)
        g1b, a1b = self._a31(self.prev_from, self.prev_to)
        self.assertEqual(g1b["at"], g1["at"])
        self.assertEqual(self._bal("output_cgst", self.prev_from, self.prev_to), D("90.00"))
        # month 2: invoice 1,000/90/90 and atadj 1,000/90/90 -> nets to 0
        g2, a2 = self._a31(self.m_from, self.m_to)
        self.assertEqual(g2["at"], [])
        self.assertEqual(len(g2["txpd"]), 1)
        self.assertEqual(D(str(g2["txpd"][0]["ad_amt"])), D("1000.00"))
        self.assertEqual((a2["txval"], a2["camt"], a2["samt"]), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(self._bal("output_cgst", self.m_from, self.m_to), a2["camt"])
        # portal-shaped export
        q = {"from": str(self.m_from), "to": str(self.m_to)}
        exp = self.client.get("/api/billing/gst/gstr1-export/", q).data      # flag OFF by default
        self.assertNotIn("txpd", exp)
        self.assertNotIn("at", exp)
        self.assertIn("AT/ATADJ", exp["_cenvora"]["advances_note"])
        with override_settings(GSTR1_ADVANCES_JSON_ENABLED=True, GSTR1_ADVANCE_ADJ_JSON_KEY="atadj"):
            exp = self.client.get("/api/billing/gst/gstr1-export/", q).data
        self.assertEqual(exp["atadj"][0]["itms"][0]["ad_amt"], 1000.0)
        self.assertEqual(exp["atadj"][0]["sply_ty"], "INTRA")
        with override_settings(GSTR1_ADVANCES_JSON_ENABLED=True):
            self.assertIn("txpd", self.client.get("/api/billing/gst/gstr1-export/", q).data)
        csv = self.client.get("/api/billing/gst/gstr1-advances-csv/", {**q, "sheet": "atadj"})
        self.assertEqual(csv.status_code, 200)
        body = csv.content.decode()
        self.assertIn("Gross Advance Adjusted", body)
        self.assertIn("29-Karnataka,,18,1000.00,0.00", body)
        self.assertIn("VERIFY", body)

    def test_goods_advance_has_no_gst_and_clears_2102(self):
        pay = self._advance("2000.00", self.today, kind="goods", mode="cash")
        self.assertEqual(self._bal("customer_advances"), D("2000.00"))
        self.assertEqual(self._bal("output_cgst"), D("0.00"))
        inv = self._invoice("R17-INV-G", "2000.00", self.today)  # 2,360
        self.assertEqual(self._allocate(pay, inv).status_code, 200)
        self.assertEqual(self._bal("customer_advances"), D("0.00"))
        adj = AdvanceAdjustment.objects.get(payment=pay)
        self.assertEqual((adj.gross, adj.taxable, adj.cgst + adj.sgst + adj.igst), (D("2000.00"), D("2000.00"), D("0.00")))
        g, _ = self._a31(self.m_from, self.m_to)
        self.assertEqual((g["at"], g["txpd"]), ([], []))
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("2000.00"))

    def test_partial_application_is_pro_rata_and_remainder_exact(self):
        pay = self._advance("1180.00", self.today)
        inv1 = self._invoice("R17-P1", "500.00", self.today)   # 590
        self.assertEqual(self._allocate(pay, inv1).status_code, 200)
        a1 = AdvanceAdjustment.objects.get(invoice=inv1)
        self.assertEqual((a1.gross, a1.taxable, a1.cgst, a1.sgst), (D("590.00"), D("500.00"), D("45.00"), D("45.00")))
        pay.refresh_from_db()
        res = self.client.get(f"/api/billing/payments/{pay.id}/").data
        self.assertEqual(D(res["unapplied_amount"]), D("590.00"))
        g, _ = self._a31(self.m_from, self.m_to)
        self.assertEqual(D(str(g["at"][0]["ad_amt"])), D("500.00"))  # unapplied part still in 'at'
        inv2 = self._invoice("R17-P2", "1000.00", self.today)  # 1,180 due, only 590 left
        self.assertEqual(self._allocate(pay, inv2).status_code, 200)
        self.assertEqual(self._bal("customer_advances"), D("0.00"))
        inv2.refresh_from_db()
        self.assertEqual(inv2.amount_paid, D("590.00"))
        g, a31 = self._a31(self.m_from, self.m_to)
        self.assertEqual((g["at"], g["txpd"]), ([], []))
        self.assertEqual((a31["txval"], a31["camt"]), (D("1500.00"), D("135.00")))

    def test_odd_paisa_partial_still_clears(self):
        pay = self._advance("1000.00", self.today)  # 847.46 + 76.27 + 76.27
        inv1 = self._invoice("R17-O1", "100.00", self.today)  # 118
        inv2 = self._invoice("R17-O2", "1000.00", self.today)
        self._allocate(pay, inv1)
        self._allocate(pay, inv2)
        self.assertEqual(self._bal("customer_advances"), D("0.00"))
        self.assertEqual(sum((a.gross for a in AdvanceAdjustment.objects.filter(payment=pay)), D("0")), D("1000.00"))

    def test_deallocation_posts_mirror_and_restores_advance(self):
        pay = self._advance("1180.00", self.today)
        inv = self._invoice("R17-D1", "1000.00", self.today)
        self._allocate(pay, inv)
        adj = AdvanceAdjustment.objects.get(payment=pay)
        self.assertEqual(self.client.delete(f"/api/billing/payments/{pay.id}/").status_code, 400)
        res = self.client.post(f"/api/billing/payments/adjustments/{adj.id}/reverse/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.client.post(f"/api/billing/payments/adjustments/{adj.id}/reverse/", {}, format="json")  # idempotent
        self.assertEqual(GeneralLedgerEntry.objects.filter(reference__startswith=f"Advance Adjustment {adj.id}").count(), 8)
        self.assertEqual(self._bal("customer_advances"), D("1000.00"))
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("0.00"))
        g, a31 = self._a31(self.m_from, self.m_to)
        self.assertEqual(D(str(g["at"][0]["ad_amt"])), D("1000.00"))
        self.assertEqual(g["txpd"], [])
        self.assertEqual(a31["camt"], self._bal("output_cgst", self.m_from, self.m_to))

    def _file(self, d):
        res = self.client.post("/api/billing/gst/gstr1-filing/", {"period": d.strftime("%Y-%m"), "filed": True,
                                                                  "confirm": True}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["filed"])

    def test_filing_flag_requires_confirmation_and_defaults_unfiled(self):
        res = self.client.get("/api/billing/gst/gstr1-filing/", {"period": self.prev_from.strftime("%Y-%m")})
        self.assertFalse(res.data["filed"])
        bad = self.client.post("/api/billing/gst/gstr1-filing/", {"period": self.prev_from.strftime("%Y-%m"),
                                                                 "filed": True}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_application_into_filed_period_rejected_open_prior_period_allowed(self):
        pay = self._advance("1180.00", self.prev_from)
        inv = self._invoice("R17-B1", "1000.00", self.prev_from)
        ok = self.client.post(f"/api/billing/payments/{pay.id}/allocate/",
                              {"invoice_id": str(inv.id), "date": str(self.prev_to)}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)  # prior month still unfiled -> allowed
        pay2 = self._advance("1180.00", self.prev_from)
        inv2 = self._invoice("R17-B2", "1000.00", self.prev_from)
        self._file(self.prev_from)
        res = self.client.post(f"/api/billing/payments/{pay2.id}/allocate/",
                               {"invoice_id": str(inv2.id), "date": str(self.prev_to)}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(AdvanceAdjustment.objects.filter(payment=pay2).count(), 0)

    def test_same_month_pair_recorded_next_month_before_filing_is_netted(self):
        pay = self._advance("1180.00", self.prev_from + timedelta(days=2))
        inv = self._invoice("R17-NM1", "1000.00", self.prev_from + timedelta(days=5))
        self.assertEqual(self._allocate(pay, inv).status_code, 200)   # recorded today (month 2)
        adj = AdvanceAdjustment.objects.get(payment=pay)
        self.assertTrue(adj.netted)
        self.assertEqual(adj.gstr1_period, self.prev_from)
        self.assertEqual(adj.recorded_on, self.today)
        g1, a1 = self._a31(self.prev_from, self.prev_to)
        g2, a2 = self._a31(self.m_from, self.m_to)
        self.assertEqual((g1["at"], g1["txpd"], g2["at"], g2["txpd"]), ([], [], [], []))
        self.assertEqual((a1["txval"], a1["camt"], a1["samt"]), (D("1000.00"), D("90.00"), D("90.00")))
        self.assertEqual(self._bal("output_cgst", self.prev_from, self.prev_to), D("90.00"))
        self.assertEqual(self._bal("output_cgst", self.m_from, self.m_to), D("0.00"))

    def test_same_month_pair_recorded_after_month1_filed_goes_to_txpd_next_month(self):
        pay = self._advance("1180.00", self.prev_from + timedelta(days=2))
        inv = self._invoice("R17-NM2", "1000.00", self.prev_from + timedelta(days=5))
        g1_before, _ = self._a31(self.prev_from, self.prev_to)
        self.assertEqual(D(str(g1_before["at"][0]["ad_amt"])), D("1000.00"))   # filed with the advance in 'at'
        self._file(self.prev_from)
        self.assertEqual(self._allocate(pay, inv).status_code, 200)
        adj = AdvanceAdjustment.objects.get(payment=pay)
        self.assertFalse(adj.netted)
        self.assertEqual((adj.gstr1_period, adj.date), (self.m_from, self.today))
        g1, a1 = self._a31(self.prev_from, self.prev_to)
        self.assertEqual(g1["at"], g1_before["at"])                     # filed month unchanged
        self.assertEqual(self._bal("output_cgst", self.prev_from, self.prev_to), D("180.00"))
        g2, a2 = self._a31(self.m_from, self.m_to)
        self.assertEqual(g2["at"], [])
        self.assertEqual(D(str(g2["txpd"][0]["ad_amt"])), D("1000.00"))
        raw = {k: D(str(v)).quantize(D("0.01")) for k, v in g2["table_3_1_a_raw"].items()}
        self.assertEqual((raw["txval"], raw["camt"]), (D("-1000.00"), D("-90.00")))
        self.assertEqual(self._bal("output_cgst", self.m_from, self.m_to), raw["camt"])
        # floor: never a negative 3.1(a); unadjusted balance carried out
        self.assertEqual((a2["txval"], a2["camt"], a2["samt"], a2["iamt"]), (D("0.00"),) * 4)
        self.assertEqual(D(str(g2["carry_forward_out"]["txval"])), D("1000.00"))
        self.assertEqual(D(str(g2["carry_forward_out"]["camt"])), D("90.00"))
        self.assertEqual(len(g2["txpd"]), 1)                              # GSTR-1 row still in full
        r = self.client.get("/api/billing/gst/gstr3b/", {"from": str(self.m_from), "to": str(self.m_to)})
        self.assertIn("advance_adjustment_carried_out", [w["code"] for w in r.data["warnings"]])
        self.assertEqual(r.data["table_3_1"]["outward_taxable"]["camt"], 0.0)

    def test_negative_31a_carries_forward_across_two_months(self):
        m1, _ = month_bounds(self.prev_from - timedelta(days=1))           # M-2
        m0, _ = month_bounds(m1 - timedelta(days=1))                       # M-3
        pay = self._advance("1180.00", m0 + timedelta(days=2))
        inv = self._invoice("R17-CF0", "1000.00", m0 + timedelta(days=5))
        self._file(m0)
        self.assertEqual(self._allocate(pay, inv).status_code, 200)
        AdvanceAdjustment.objects.filter(payment=pay).update(gstr1_period=m1, date=m1 + timedelta(days=3))
        _, am1 = self._a31(m1, month_bounds(m1)[1])
        self.assertEqual(am1["txval"], D("0.00"))                          # floored, 1000 carried
        self._invoice("R17-CF1", "600.00", self.prev_from + timedelta(days=3))
        gp, ap = self._a31(self.prev_from, self.prev_to)
        self.assertEqual((ap["txval"], ap["camt"]), (D("0.00"), D("0.00")))
        self.assertEqual(D(str(gp["carry_forward_in"]["txval"])), D("1000.00"))
        self.assertEqual(D(str(gp["carry_forward_out"]["txval"])), D("400.00"))
        self.assertEqual(gp["carry_forward_from"], m1.strftime("%b %Y"))
        self._invoice("R17-CF2", "1000.00", self.today)
        gc, ac = self._a31(self.m_from, self.m_to)
        self.assertEqual((ac["txval"], ac["camt"], ac["samt"]), (D("600.00"), D("54.00"), D("54.00")))
        self.assertEqual(D(str(gc["carry_forward_in"]["camt"])), D("36.00"))
        self.assertEqual(gc["carry_forward_from"], m1.strftime("%b %Y"))
        self.assertEqual(D(str(gc["carry_forward_out"]["txval"])), D("0.00"))
        r = self.client.get("/api/billing/gst/gstr3b/", {"from": str(self.m_from), "to": str(self.m_to)})
        msg = [w["message"] for w in r.data["warnings"] if w["code"] == "advance_adjustment_carried_in"][0]
        self.assertIn("₹400.00 of advance adjustment carried forward from " + m1.strftime("%b %Y"), msg)

    def test_qrmp_company_treats_quarter_as_unfiled(self):
        from billing.advances import is_gstr1_filed, is_qrmp
        self._file(self.prev_from)
        self.assertTrue(is_gstr1_filed(self.tenant, self.prev_from))
        self.assertFalse(is_qrmp(self.tenant))                 # no filing-frequency setting exists yet
        self.tenant.gst_filing_frequency = "quarterly"          # simulated future setting
        self.assertTrue(is_qrmp(self.tenant))
        self.assertFalse(is_gstr1_filed(self.tenant, self.prev_from))

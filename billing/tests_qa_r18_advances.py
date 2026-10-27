"""QA R18: receipts vs advances (no auto-apply to later invoices), GSTR-1 at/txpd on screen + CSV,
partial allocation, filed-month lock on advances, invoice status with advance adjustments."""
from datetime import timedelta
from decimal import Decimal

from billing.models import GSTR1Filing, Payment, SalesInvoice
from billing import tests_advance_adjustment as _r17
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry

D = Decimal
SVC = {"supply_kind": "services", "gst_rate": "18", "place_of_supply": "Karnataka"}
FILED = "This month's GSTR-1 is filed. Record the correction in the current month"


class R18AdvanceTests(FourFixesBase):
    # Reuse the R17 fixtures/helpers without re-running the R17 test methods.
    def setUp(self):
        super().setUp()
        self.today = _r17.today_ist()
        self.m_from, self.m_to = _r17.month_bounds(self.today)
        self.prev_from, self.prev_to = _r17.month_bounds(self.m_from - timedelta(days=1))
        self.cust = _r17.Customer.objects.create(name="R18 Service Cust", state="Karnataka", created_by=self.tenant)
        self.acc = _r17.AccountingService.get_or_create_default_accounts(self.tenant)

    _advance = _r17.AdvanceAdjustmentTests._advance
    _invoice = _r17.AdvanceAdjustmentTests._invoice
    _allocate = _r17.AdvanceAdjustmentTests._allocate

    def _receipt(self, amount, on, **extra):
        body = {"customer": str(self.cust.id), "date": str(on), "amount": str(amount), "mode": "upi"}
        body.update(extra)
        return self.client.post("/api/billing/payments/", body, format="json")

    def _receipt_lines(self, pay):
        return GeneralLedgerEntry.objects.filter(reference=f"Payment Received {pay.id}")

    def _credit(self, pay, key):
        return sum((l.credit - l.debit for l in self._receipt_lines(pay) if l.account_id == self.acc[key].id), D("0"))

    # P0-A
    def test_receipt_before_invoice_date_is_not_auto_applied(self):
        inv = self._invoice("R18-INV-2", "1000.00", self.today)
        res = self._receipt("1180.00", self.prev_from + timedelta(days=14), auto_allocate=True, **SVC)
        self.assertEqual(res.status_code, 201, res.data)
        pay = Payment.objects.get(pk=res.data["id"])
        self.assertIsNone(pay.invoice_id)
        self.assertEqual(Payment.objects.filter(customer=self.cust).count(), 1)
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("0"))

    def test_recorded_advance_is_never_auto_applied_services_and_goods(self):
        self._invoice("R18-INV-OLD", "1000.00", self.prev_from)  # open, dated before the receipts
        svc = self._receipt("1180.00", self.today, **SVC)
        gds = self._receipt("2000.00", self.today, supply_kind="goods")  # legacy client: no auto_allocate
        self.assertEqual(svc.status_code, 201, svc.data)
        self.assertEqual(gds.status_code, 201, gds.data)
        svc, gds = Payment.objects.get(pk=svc.data["id"]), Payment.objects.get(pk=gds.data["id"])
        self.assertIsNone(svc.invoice_id)
        self.assertIsNone(gds.invoice_id)
        # Service advance: Cr 2102 taxable + output tax (18% inclusive, intra-state Karnataka)
        self.assertEqual(self._credit(svc, "customer_advances"), D("1000.00"))
        self.assertEqual(self._credit(svc, "output_cgst") + self._credit(svc, "output_sgst"), D("180.00"))
        # Goods advance: Cr 2102 gross, no GST (Notfn 66/2017)
        self.assertEqual(self._credit(gds, "customer_advances"), D("2000.00"))
        self.assertEqual(self._credit(gds, "output_cgst") + self._credit(gds, "output_sgst")
                         + self._credit(gds, "output_igst"), D("0"))

    def test_plain_receipt_still_fifo_to_earlier_invoice(self):
        inv = self._invoice("R18-INV-OLD", "1000.00", self.prev_from)
        later = self._invoice("R18-INV-NEW", "1000.00", self.today)
        res = self._receipt("1180.00", self.today, auto_allocate=True)  # exactly covered: no remainder
        self.assertEqual(res.status_code, 201, res.data)
        inv.refresh_from_db(); later.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("1180.00"))
        # Same-day invoice is allowed (invoice_date <= receipt date); nothing left over for it here
        self.assertEqual(later.amount_paid, D("0"))

    def test_manual_link_to_later_invoice_rejected(self):
        inv = self._invoice("R18-INV-2", "1000.00", self.today)
        res = self._receipt("500.00", self.prev_from, invoice=str(inv.id))
        self.assertEqual(res.status_code, 400)
        self.assertIn("advance", str(res.data).lower())

    # P0-B
    def test_at_and_atadj_csv_and_on_screen_rows_across_months(self):
        adv_date = self.prev_from + timedelta(days=14)
        pay = self._advance("1180.00", adv_date)
        prev = self.client.get("/api/billing/gst/gstr1-advances-csv/",
                               {"from": str(self.prev_from), "to": str(self.prev_to), "sheet": "at"})
        self.assertEqual(prev.status_code, 200)
        body = prev.content.decode()
        self.assertIn("1000.00", body, body)  # taxable value of the Sept-style advance
        js = self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.prev_from), "to": str(self.prev_to)})
        self.assertEqual(js.status_code, 200)
        meta = js.data["_cenvora"]
        self.assertTrue(meta["has_advances"])
        self.assertEqual(meta["advances"]["at_total"]["ad_amt"], 1000.0)
        self.assertNotIn("at", js.data)  # flag OFF: not in the portal JSON

        inv = self._invoice("R18-INV-2", "1000.00", self.today)
        self.assertEqual(self._allocate(pay, inv).status_code, 200)
        cur = self.client.get("/api/billing/gst/gstr1-advances-csv/",
                              {"from": str(self.m_from), "to": str(self.m_to), "sheet": "atadj"})
        self.assertIn("1000.00", cur.content.decode())
        js = self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.m_from), "to": str(self.m_to)})
        self.assertEqual(js.data["_cenvora"]["advances"]["txpd_total"]["ad_amt"], 1000.0)
        self.assertEqual(js.data["_cenvora"]["advances"]["at"], [])

    def test_empty_month_still_returns_advance_sections(self):
        js = self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.m_from), "to": str(self.m_to)})
        self.assertEqual(js.status_code, 200)
        self.assertEqual(js.data["_cenvora"]["advances"]["at"], [])
        self.assertEqual(js.data["_cenvora"]["advances"]["txpd"], [])

    # P1 partial allocation
    def test_partial_allocation_amount_validated(self):
        pay = self._advance("1180.00", self.today)
        inv = self._invoice("R18-INV-1", "500.00", self.today)  # 590 due
        self.assertEqual(self._allocate(pay, inv, amount="600.00").status_code, 400)   # > invoice due
        self.assertEqual(self._allocate(pay, inv, amount="1200.00").status_code, 400)  # > unapplied
        self.assertEqual(self._allocate(pay, inv, amount="0").status_code, 400)
        res = self._allocate(pay, inv, amount="300.00")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(D(res.data["unapplied"]), D("880.00"))
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("300.00"))
        # P1 status: partly covered by an advance adjustment -> partial_paid, then paid
        self.assertEqual(inv.payment_status, "partial_paid")
        self.assertEqual(self._allocate(pay, inv, amount="290.00").status_code, 200)
        inv.refresh_from_db()
        self.assertEqual(inv.payment_status, "paid")

    # P1 filed-month lock
    def test_filed_month_blocks_advance_create_edit_delete(self):
        adv_date = self.prev_from + timedelta(days=5)
        pay = self._advance("1180.00", adv_date)
        GSTR1Filing.objects.create(created_by=self.tenant, period=self.prev_from, filed_on=self.today)
        res = self._receipt("118.00", self.prev_from + timedelta(days=19), **SVC)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["date"][0], FILED)
        put = self.client.put(f"/api/billing/payments/{pay.id}/", {
            "customer": str(self.cust.id), "date": str(adv_date), "amount": "2000.00", "mode": "upi",
            **SVC}, format="json")
        self.assertEqual(put.status_code, 400)
        dele = self.client.delete(f"/api/billing/payments/{pay.id}/")
        self.assertEqual(dele.status_code, 400)
        self.assertEqual(dele.data["error"], FILED)
        self.assertTrue(Payment.objects.filter(pk=pay.pk).exists())
        # An unfiled month is unaffected
        self.assertEqual(self._receipt("118.00", self.today, **SVC).status_code, 201)

    # ── Rudra CA corrections ──
    def test_receipt_with_unclassified_remainder_rejected(self):
        inv = self._invoice("R18-INV-OLD", "1000.00", self.prev_from)  # 1180 due
        res = self._receipt("2000.00", self.today, auto_allocate=True)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("remainder", res.data)
        self.assertEqual(Payment.objects.filter(customer=self.cust).count(), 0)  # nothing half-saved
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("0"))
        # services without rate/POS also rejected
        res = self._receipt("2000.00", self.today, auto_allocate=True, supply_kind="services")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Payment.objects.filter(customer=self.cust).count(), 0)
        # classified remainder accepted: 1180 to the invoice, 820 stays a service advance with a voucher
        res = self._receipt("2000.00", self.today, auto_allocate=True, **SVC)
        self.assertEqual(res.status_code, 201, res.data)
        adv = Payment.objects.get(customer=self.cust, invoice__isnull=True)
        self.assertEqual(adv.amount, D("820.00"))
        self.assertTrue(adv.receipt_voucher_number.startswith("RV"))

    def test_service_advance_unknown_rate_and_pos_posts_18pct_igst(self):
        res = self._receipt("1180.00", self.today, supply_kind="services", gst_rate="unknown",
                            place_of_supply="unknown", auto_allocate=False)
        self.assertEqual(res.status_code, 201, res.data)
        pay = Payment.objects.get(pk=res.data["id"])
        self.assertEqual(pay.gst_rate, D("18"))
        self.assertEqual(self._credit(pay, "customer_advances"), D("1000.00"))
        self.assertEqual(self._credit(pay, "output_igst"), D("180.00"))
        self.assertEqual(self._credit(pay, "output_cgst"), D("0"))

    def test_receipt_voucher_sequential_and_viewable(self):
        a = Payment.objects.get(pk=self._receipt("118.00", self.today, auto_allocate=False, **SVC).data["id"])
        b = Payment.objects.get(pk=self._receipt("236.00", self.today, auto_allocate=False, **SVC).data["id"])
        g = Payment.objects.get(pk=self._receipt("500.00", self.today, auto_allocate=False, supply_kind="goods").data["id"])
        self.assertEqual(a.receipt_voucher_number, "RV-001")
        self.assertEqual(b.receipt_voucher_number, "RV-002")
        self.assertEqual(g.receipt_voucher_number, "")
        html = self.client.get(f"/api/billing/payments/{b.id}/receipt-voucher/")
        self.assertEqual(html.status_code, 200)
        self.assertIn("RV-002", html.content.decode())
        self.assertEqual(self.client.get(f"/api/billing/payments/{g.id}/receipt-voucher/").status_code, 404)

    def test_advance_defaults_from_past_invoice_lines(self):
        self._invoice("R18-D1", "100.00", self.prev_from)
        res = self.client.get("/api/billing/payments/advance-defaults/", {"customer": str(self.cust.id)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["supply_kind"], "services")  # SAC 998311
        self.assertTrue(res.data["needs_confirmation"])

    def test_allocate_advance_to_later_invoice_lands_in_txpd_for_invoice_month(self):
        pay = self._advance("1180.00", self.prev_from + timedelta(days=3))
        inv = self._invoice("R18-LATER", "1000.00", self.today)
        res = self._allocate(pay, inv)
        self.assertEqual(res.status_code, 200, res.data)
        js = self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.m_from), "to": str(self.m_to)})
        self.assertEqual(js.data["_cenvora"]["advances"]["txpd_total"]["ad_amt"], 1000.0)
        prev = self.client.get("/api/billing/gst/gstr1-export/", {"from": str(self.prev_from), "to": str(self.prev_to)})
        self.assertEqual(prev.data["_cenvora"]["advances"]["txpd"], [])

    def test_receipt_edit_reverses_and_reposts_never_deletes(self):
        pay = self._advance("1180.00", self.today)
        before = list(self._receipt_lines(pay).values_list("id", flat=True))
        put = self.client.put(f"/api/billing/payments/{pay.id}/", {
            "customer": str(self.cust.id), "date": str(self.today), "amount": "2360.00", "mode": "upi",
            **SVC}, format="json")
        self.assertEqual(put.status_code, 200, put.data)
        ids = set(GeneralLedgerEntry.objects.filter(reference__startswith=f"Payment Received {pay.id}")
                  .values_list("id", flat=True))
        self.assertTrue(set(before) <= ids)  # originals kept
        rev = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {pay.id} rev 1")
        self.assertTrue(rev.exists())
        net = lambda key: sum((l.credit - l.debit for l in GeneralLedgerEntry.objects.filter(
            reference__startswith=f"Payment Received {pay.id}", account=self.acc[key])), D("0"))
        self.assertEqual(net("customer_advances"), D("2000.00"))
        self.assertEqual(net("output_cgst") + net("output_sgst"), D("360.00"))
        pay.refresh_from_db()
        self.assertEqual(pay.receipt_voucher_number, "RV-001")  # voucher number kept on edit

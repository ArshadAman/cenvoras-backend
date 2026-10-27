"""QA R18: receipts vs advances (no auto-apply to later invoices), GSTR-1 at/txpd on screen + CSV,
partial allocation, filed-month lock on advances, invoice status with advance adjustments."""
from datetime import timedelta
from decimal import Decimal

from billing.models import GSTR1Filing, Payment, SalesInvoice
from billing import tests_advance_adjustment as _r17
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry

D = Decimal


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
        res = self._receipt("1180.00", self.prev_from + timedelta(days=14))  # no supply_kind, auto_allocate default
        self.assertEqual(res.status_code, 201, res.data)
        pay = Payment.objects.get(pk=res.data["id"])
        self.assertIsNone(pay.invoice_id)
        self.assertEqual(Payment.objects.filter(customer=self.cust).count(), 1)
        inv.refresh_from_db()
        self.assertEqual(inv.amount_paid, D("0"))

    def test_recorded_advance_is_never_auto_applied_services_and_goods(self):
        self._invoice("R18-INV-OLD", "1000.00", self.prev_from)  # open, dated before the receipts
        svc = self._receipt("1180.00", self.today, supply_kind="services")
        gds = self._receipt("2000.00", self.today, supply_kind="goods")
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
        res = self._receipt("1180.00", self.today - timedelta(days=0))
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
        res = self._receipt("118.00", self.prev_from + timedelta(days=19), supply_kind="services")
        self.assertEqual(res.status_code, 400)
        self.assertIn("filed", str(res.data))
        put = self.client.put(f"/api/billing/payments/{pay.id}/", {
            "customer": str(self.cust.id), "date": str(adv_date), "amount": "2000.00", "mode": "upi",
            "supply_kind": "services"}, format="json")
        self.assertEqual(put.status_code, 400)
        dele = self.client.delete(f"/api/billing/payments/{pay.id}/")
        self.assertEqual(dele.status_code, 400)
        self.assertIn("filed", dele.data["error"])
        self.assertTrue(Payment.objects.filter(pk=pay.pk).exists())
        # An unfiled month is unaffected
        self.assertEqual(self._receipt("118.00", self.today, supply_kind="services").status_code, 201)

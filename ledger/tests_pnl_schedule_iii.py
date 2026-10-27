"""R17 P0: P&L must not double-count purchases (Schedule III presentation)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache

from billing.models import PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Vendor
from billing.tests_four_fixes import FourFixesBase
from ledger import pnl
from ledger.models import Account, AccountType, GeneralLedgerEntry
from ledger.services import AccountingService

D = Decimal


class ScheduleIIIPnLTests(FourFixesBase):
    """Opening 100 units brought in; buy 190 @500 (95,000); return 9 (4,500); sell 81 @1,000."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.d0 = date.today() - timedelta(days=10)
        self.d1 = date.today() - timedelta(days=5)
        vendor = Vendor.objects.create(name="KA Vendor", gstin="29AAAAA1111A1Z5", state="Karnataka",
                                       created_by=self.tenant)
        bill = PurchaseBill.objects.create(
            created_by=self.tenant, bill_number="PB-PNL-1", bill_date=self.d0, vendor=vendor,
            vendor_name=vendor.name, vendor_gstin=vendor.gstin, place_of_supply="Karnataka",
            total_amount=D("112100.00"))
        PurchaseBillItem.objects.create(purchase_bill=bill, product=self.product, quantity=190,
                                        price=D("500.00"), tax=D("18.00"), amount=D("112100.00"),
                                        hsn_sac_code="847130")
        AccountingService.create_purchase_bill_entries(bill)
        res = self.client.post("/api/billing/debit-notes/", {
            "date": str(self.d1), "original_bill": str(bill.id), "vendor_name": vendor.name, "reason": "return",
            "total_amount": "5310.00",
            "items": [{"product": str(self.product.id), "quantity": 9, "price": "500.00", "tax": "18.00",
                       "amount": "5310.00"}]}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        inv = SalesInvoice.objects.create(
            created_by=self.tenant, customer=self.customer, customer_name=self.customer.name,
            invoice_number="INV-PNL-1", invoice_date=self.d1, place_of_supply="Karnataka", status="final",
            total_amount=D("95580.00"))
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, quantity=81,
                                        price=D("1000.00"), tax=D("18.00"), amount=D("95580.00"),
                                        hsn_sac_code="847130")
        AccountingService.create_sales_invoice_entries(inv)
        self.product.refresh_from_db()
        cache.clear()

    def _gl_expense_total(self, frm=None, to=None):
        total = D("0")
        for acc in Account.objects.filter(created_by=self.tenant, account_type=AccountType.EXPENSE):
            qs = GeneralLedgerEntry.objects.filter(account=acc, created_by=self.tenant)
            if frm:
                qs = qs.filter(date__gte=frm)
            if to:
                qs = qs.filter(date__lte=to)
            total += sum((e.debit - e.credit for e in qs), D("0"))
        return total

    def test_stock_rollforward(self):
        self.assertEqual(D(str(self.product.stock)), D("200"))
        inv = pnl.inventory_change(self.tenant)
        self.assertEqual(inv["opening"], D("50000.00"))   # 100 x 500
        self.assertEqual(inv["closing"], D("100000.00"))  # 200 x 500
        self.assertEqual(inv["change"], D("-50000.00"))

    def test_statement_has_no_cogs_line_and_ties_to_tb_plus_stock_change(self):
        res = self.client.get("/api/ledger/profit-loss/")
        self.assertEqual(res.status_code, 200)
        names = [i["name"] for i in res.data["expenses"]["items"]]
        codes = [i["code"] for i in res.data["expenses"]["items"]]
        self.assertNotIn("COGS", codes)
        self.assertFalse(any("Cost of Goods Sold" in n for n in names))
        by_id = {i["id"]: D(str(i["amount"])) for i in res.data["expenses"]["items"]}
        self.assertEqual(by_id["purchases-stock-in-trade"], D("90500.00"))   # 95,000 - 4,500
        self.assertEqual(by_id["changes-in-inventories"], D("-50000.00"))
        tb_exp = self._gl_expense_total()
        self.assertEqual(tb_exp, D("90500.00"))
        self.assertEqual(D(str(res.data["expenses"]["total"])), tb_exp + D("-50000.00"))  # 40,500
        self.assertEqual(D(str(res.data["revenue"]["total"])), D("81000.00"))
        self.assertEqual(D(str(res.data["net_profit"])), D("40500.00"))
        self.assertEqual(D(str(res.data["memo"]["cogs_estimate"])), D("40500.00"))  # memo only

    def test_period_opening_uses_stock_on_day_before(self):
        res = self.client.get("/api/ledger/profit-loss/", {"from": str(self.d1), "to": str(date.today())})
        inv = res.data["inventory"]
        # Before d1: 100 + 190 = 290 units -> 1,45,000 ; closing 200 -> 1,00,000
        self.assertEqual(D(str(inv["opening_stock"])), D("145000.00"))
        self.assertEqual(D(str(inv["closing_stock"])), D("100000.00"))
        by_id = {i["id"]: D(str(i["amount"])) for i in res.data["expenses"]["items"]}
        self.assertEqual(by_id["purchases-stock-in-trade"], D("-4500.00"))
        self.assertEqual(D(str(res.data["net_profit"])), D("81000.00") - (D("-4500.00") + D("45000.00")))

    def test_balance_sheet_still_balances_and_re_matches_pnl(self):
        res = self.client.get("/api/ledger/balance-sheet/")
        self.assertTrue(res.data["is_balanced"], res.data)
        self.assertEqual(D(str(res.data["equity"]["retained_earnings"])), D("40500.00"))
        inv_line = [i for i in res.data["assets"]["items"] if i["id"] == "inventories"][0]
        self.assertEqual(D(str(inv_line["amount"])), D("100000.00"))

    def test_switch_to_gl_only_basis(self):
        with mock.patch.object(pnl, "PNL_PRESENTATION", "cogs"):
            res = self.client.get("/api/ledger/profit-loss/")
        self.assertEqual(res.data["basis"], "cogs")
        self.assertEqual(D(str(res.data["net_profit"])), D("81000.00") - D("90500.00"))
        self.assertFalse(any(i["id"] == "changes-in-inventories" for i in res.data["expenses"]["items"]))

    def test_dashboard_today_uses_same_helper(self):
        rev, exp, net, _ = pnl.compute_net_profit(self.tenant, from_date=self.d1, to_date=self.d1)
        self.assertEqual(net, D("81000.00") - (D("-4500.00") + D("45000.00")))

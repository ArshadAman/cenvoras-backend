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


from inventory.models import Product  # noqa: E402


class ScheduleIIITwoYearAndStockGuardTests(FourFixesBase):
    """Go-live 100 units. FY24-25: buy 190 @500, sell 81. FY25-26: sell 50."""

    FY1 = (date(2024, 4, 1), date(2025, 3, 31))
    FY2 = (date(2025, 4, 1), date(2026, 3, 31))

    def setUp(self):
        super().setUp()
        cache.clear()

    def _bill(self, num, on, qty, price, product=None, discount="0"):
        vendor, _ = Vendor.objects.get_or_create(name="KA Vendor", gstin="29AAAAA1111A1Z5", state="Karnataka",
                                                 created_by=self.tenant)
        taxable = D(qty) * D(price) * (100 - D(discount)) / 100
        bill = PurchaseBill.objects.create(created_by=self.tenant, bill_number=num, bill_date=on, vendor=vendor,
                                           vendor_name=vendor.name, vendor_gstin=vendor.gstin,
                                           place_of_supply="Karnataka", total_amount=taxable * D("1.18"))
        PurchaseBillItem.objects.create(purchase_bill=bill, product=product or self.product, quantity=qty,
                                        price=D(price), discount=D(discount), tax=D("18.00"),
                                        amount=taxable * D("1.18"), hsn_sac_code="847130")
        AccountingService.create_purchase_bill_entries(bill)

    def _sale(self, num, on, qty, price="1000.00", product=None):
        inv = SalesInvoice.objects.create(created_by=self.tenant, customer=self.customer,
                                          customer_name=self.customer.name, invoice_number=num, invoice_date=on,
                                          place_of_supply="Karnataka", status="final", total_amount=D("0"))
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=product or self.product, quantity=qty,
                                        price=D(price), tax=D("18.00"), amount=D(qty) * D(price) * D("1.18"),
                                        hsn_sac_code="847130")
        inv.refresh_from_db()
        AccountingService.create_sales_invoice_entries(inv)
        cache.clear()

    def _pnl(self, frm, to):
        return self.client.get("/api/ledger/profit-loss/", {"from": str(frm), "to": str(to)}).data

    def _bs(self, as_of):
        return self.client.get("/api/ledger/balance-sheet/", {"as_of": str(as_of)}).data

    def test_two_financial_years_roll_closing_to_opening_and_single_equity_adjustment(self):
        self._bill("PB-FY1", date(2024, 6, 1), 190, "500.00")
        self._sale("INV-FY1", date(2024, 9, 1), 81)
        self._sale("INV-FY2", date(2025, 6, 1), 50)
        p1, p2 = self._pnl(*self.FY1), self._pnl(*self.FY2)
        self.assertEqual(D(str(p1["inventory"]["opening_stock"])), D("50000.00"))
        self.assertEqual(D(str(p1["inventory"]["closing_stock"])), D("104500.00"))   # 209 x 500
        self.assertEqual(D(str(p2["inventory"]["opening_stock"])), D("104500.00"))  # = FY1 closing
        self.assertEqual(D(str(p2["inventory"]["closing_stock"])), D("79500.00"))   # 159 x 500
        self.assertEqual(D(str(p2["net_profit"])), D("50000.00") - D("25000.00"))
        b1, b2 = self._bs(self.FY1[1]), self._bs(self.FY2[1])
        for b in (b1, b2):
            self.assertTrue(b["is_balanced"], b)
            adj = [i for i in b["equity"]["items"] if i["id"] == "opening-stock-adjustment"]
            self.assertEqual(len(adj), 1)
            self.assertEqual(D(str(adj[0]["amount"])), D("50000.00"))   # go-live only, never grows
            self.assertEqual(adj[0]["group"], "Reserves & Surplus")
        inv2 = [i for i in b2["assets"]["items"] if i["id"] == "inventories"][0]
        self.assertEqual((inv2["name"], inv2["group"]), ("Inventories (stock-in-trade)", "Current assets"))
        self.assertEqual(D(str(inv2["amount"])), D("79500.00"))
        # Profit flows into RE: FY1 RE + FY2 profit = FY2 RE
        self.assertEqual(D(str(b2["equity"]["retained_earnings"])) - D(str(b1["equity"]["retained_earnings"])),
                         D(str(p2["net_profit"])))

    def test_negative_total_stock_shows_zero_with_incomplete_banner(self):
        Product.objects.filter(pk=self.product.pk).update(price=D("500.00"))
        self._sale("INV-NEG", date(2024, 9, 1), 300)   # 100 on hand -> -200
        p = self._pnl(*self.FY1)
        self.assertEqual(D(str(p["inventory"]["closing_stock"])), D("0.00"))
        self.assertTrue(p["inventory"]["stock_data_incomplete"])
        self.assertTrue(any("Stock data incomplete" in w for w in p["inventory"]["warnings"]))
        self.assertTrue(any("negative stock" in w for w in p["inventory"]["warnings"]))
        b = self._bs(self.FY1[1])
        inv = [i for i in b["assets"]["items"] if i["id"] == "inventories"][0]
        self.assertEqual(D(str(inv["amount"])), D("0.00"))
        self.assertTrue(b["inventory"]["stock_data_incomplete"])
        self.assertTrue(b["is_balanced"], b)

    def test_negative_item_valued_at_last_wac_not_zero(self):
        other = Product.objects.create(name="Gadget", hsn_sac_code="847130", sale_price=D("1000.00"),
                                       tax=D("18.00"), unit="pcs", stock=0, created_by=self.tenant)
        self._bill("PB-G", date(2024, 5, 1), 10, "200.00", product=other)
        self._sale("INV-G", date(2024, 6, 1), 15, product=other)   # Gadget -5 @200 = -1,000
        self._bill("PB-W", date(2024, 5, 1), 10, "500.00")         # Widget 110 @ WAC
        p = self._pnl(*self.FY1)
        neg = p["inventory"]["negative_stock_items"]
        self.assertEqual([(n["product"], D(n["value"])) for n in neg], [("Gadget", D("-1000.00"))])
        self.assertFalse(p["inventory"]["stock_data_incomplete"])
        self.assertEqual(D(str(p["inventory"]["closing_stock"])), D("110") * D("500.00") - D("1000.00"))

    def test_wac_excludes_gst_and_nets_discount(self):
        self._bill("PB-D", date(2024, 5, 1), 10, "500.00", discount="10")
        p = self._pnl(*self.FY1)
        self.assertEqual(D(str(p["inventory"]["closing_stock"])), D("110") * D("450.00"))

    def test_guard_warns_when_inventory_ledger_has_journals(self):
        acc = AccountingService.get_or_create_default_accounts(self.tenant)["1300"]
        GeneralLedgerEntry.objects.create(date=date(2024, 4, 1), account=acc, debit=D("50000.00"), credit=0,
                                          description="Opening stock", reference="OB", created_by=self.tenant)
        p = self._pnl(*self.FY1)
        self.assertTrue(any("double-counted" in w for w in p["inventory"]["warnings"]))

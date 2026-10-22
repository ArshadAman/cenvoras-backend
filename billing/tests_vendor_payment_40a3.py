"""Vendor Payments: s.40A(3) warning for cash payments over ₹10,000 (QA P0-4e)."""
from decimal import Decimal
from datetime import date

from billing.models import PurchaseBill, PurchaseBillItem, Vendor
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService


class VendorPayment40A3Tests(FourFixesBase):
    def _bill(self, number, price):
        vendor = Vendor.objects.create(name=f"V {number}", gstin=f"29AAAAA{number[-4:]}A1Z5", state="Karnataka", created_by=self.tenant)
        total = (price * Decimal("1.18")).quantize(Decimal("0.01"))
        bill = PurchaseBill.objects.create(
            created_by=self.tenant, bill_number=number, bill_date=date.today(), vendor=vendor,
            vendor_name=vendor.name, vendor_gstin=vendor.gstin, total_amount=total,
        )
        PurchaseBillItem.objects.create(purchase_bill=bill, product=self.product, quantity=1, price=price,
                                        tax=Decimal("18.00"), amount=total, hsn_sac_code="847130")
        AccountingService.create_purchase_bill_entries(bill)
        return bill, total

    def _pay(self, code, bill, amount):
        acc = AccountingService.get_or_create_default_accounts(self.tenant).get(code)
        GeneralLedgerEntry.objects.create(date=date.today(), account=acc, debit=Decimal("100000.00"), credit=0,
                                          description="Seed", reference=f"SEED-{code}", created_by=self.tenant)
        return self.client.post("/api/billing/vendor-payments/", {
            "bill_id": str(bill.id), "amount": str(amount), "payment_account_id": str(acc.id),
            "mode": "cash" if code == "1001" else "bank_transfer",
        }, format="json")

    def test_cash_over_10k_warns(self):
        bill, total = self._bill("R13-0001", Decimal("10000.00"))  # ₹11,800 like R13-P0-IGST
        res = self._pay("1001", bill, total)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(any("40A(3)" in w for w in res.data["warnings"]), res.data)

    def test_cash_at_or_below_10k_no_warning(self):
        bill, _ = self._bill("R13-0002", Decimal("9000.00"))
        res = self._pay("1001", bill, Decimal("10000.00"))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["warnings"], [])

    def test_bank_over_10k_no_warning(self):
        bill, total = self._bill("R13-0003", Decimal("10000.00"))
        res = self._pay("1002", bill, total)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["warnings"], [])

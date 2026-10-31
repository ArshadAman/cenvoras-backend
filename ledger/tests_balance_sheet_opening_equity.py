"""Balance sheet splits lifetime P&L into prior-year opening equity vs current-year RE."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService
from users.models import User


class BalanceSheetOpeningEquityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="bs_equity_tenant",
            email="bs_equity@cenvora.com",
            password="securepassword123",
            business_name="BS Equity Pvt Ltd",
            state="Karnataka",
        )
        self.client.force_authenticate(user=self.tenant)
        self.accounts = AccountingService.get_or_create_default_accounts(self.tenant)
        cash = self.accounts["1001"]
        sales = self.accounts["4001"]

        GeneralLedgerEntry.objects.create(
            date=date(2025, 6, 15),
            account=cash,
            debit=Decimal("10000.00"),
            credit=Decimal("0.00"),
            description="Prior FY cash",
            reference="PRIOR-CASH",
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=date(2025, 6, 15),
            account=sales,
            debit=Decimal("0.00"),
            credit=Decimal("10000.00"),
            description="Prior FY revenue",
            reference="PRIOR-REV",
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=date(2026, 5, 10),
            account=cash,
            debit=Decimal("3000.00"),
            credit=Decimal("0.00"),
            description="Current FY cash",
            reference="CUR-CASH",
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=date(2026, 5, 10),
            account=sales,
            debit=Decimal("0.00"),
            credit=Decimal("3000.00"),
            description="Current FY revenue",
            reference="CUR-REV",
            created_by=self.tenant,
        )

    def test_current_year_re_excludes_prior_profit(self):
        as_of = "2026-10-31"
        res = self.client.get("/api/ledger/balance-sheet/", {"as_of": as_of})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        retained = Decimal(str(res.data["equity"]["retained_earnings"]))
        self.assertEqual(retained, Decimal("3000.00"))

        owner = next(
            (
                item
                for item in res.data["equity"]["items"]
                if (item.get("code") or "").strip() == "3001"
                or "owner" in (item.get("name") or "").lower()
                or "opening equity" in (item.get("name") or "").lower()
            ),
            None,
        )
        self.assertIsNotNone(owner)
        self.assertEqual(Decimal(str(owner["amount"])), Decimal("10000.00"))

        self.assertTrue(res.data["is_balanced"])
        assets = Decimal(str(res.data["assets"]["total"]))
        liabilities = Decimal(str(res.data["liabilities"]["total"]))
        equity = Decimal(str(res.data["equity"]["total"]))
        self.assertEqual(assets, liabilities + equity)

        pl = self.client.get(
            "/api/ledger/profit-loss/",
            {"from": "2026-04-01", "to": as_of},
        )
        self.assertEqual(pl.status_code, status.HTTP_200_OK, pl.data)
        self.assertEqual(Decimal(str(pl.data["net_profit"])), retained)

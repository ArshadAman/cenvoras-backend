"""Salaries Payable stays a credit until paid; finalise accrues; pay without accrual is blocked."""
import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from hr.models import Department, Designation, Employee, PayrollRun, Payslip
from hr.services.hr_accounting_service import HRAccountingService
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService
from users.models import User


class SalariesPayableTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username="sal_pay",
            email="salpay@ex.com",
            password="x",
            business_name="Sal Pay Co",
            role="admin",
            state="Karnataka",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        dept = Department.objects.create(tenant=self.tenant, name="Ops")
        desig = Designation.objects.create(tenant=self.tenant, name="Staff")
        self.emp = Employee.objects.create(
            tenant=self.tenant,
            full_name="Paid Emp",
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender="M",
            employment_type="full_time",
            department=dept,
            designation=desig,
            work_state="Karnataka",
            status="active",
        )
        self.run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=9,
            year=2026,
            status="completed",
            total_net=Decimal("45000.00"),
        )
        Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=self.run,
            employee=self.emp,
            present_days=Decimal("26"),
            total_working_days=26,
            lop_days=Decimal("0"),
            gross_salary=Decimal("50000"),
            earnings={},
            deductions={},
            net_salary=Decimal("45000.00"),
        )
        self.accounts = AccountingService.get_or_create_default_accounts(self.tenant)
        HRAccountingService.get_or_create_hr_accounts(self.tenant)
        self.bank = self.accounts.get("1002")
        GeneralLedgerEntry.objects.create(
            date=datetime.date.today(),
            account=self.bank,
            debit=Decimal("100000.00"),
            credit=0,
            description="seed bank",
            reference="SEED-BANK",
            created_by=self.tenant,
        )

    def _payable_entries(self):
        return GeneralLedgerEntry.objects.filter(
            created_by=self.tenant, account__code="2250"
        )

    def _payable_net(self):
        rows = self._payable_entries()
        dr = sum((r.debit for r in rows), Decimal("0"))
        cr = sum((r.credit for r in rows), Decimal("0"))
        return cr - dr

    def test_finalise_creates_credit_salaries_payable(self):
        res = self.client.post(f"/api/hr/payroll-runs/{self.run.id}/finalise/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "finalised")
        accrual = GeneralLedgerEntry.objects.filter(
            created_by=self.tenant, reference="PAYROLL-ACCRUAL-2026-09", account__code="2250"
        )
        self.assertEqual(accrual.count(), 1)
        self.assertEqual(accrual.get().credit, Decimal("45000.00"))
        self.assertEqual(accrual.get().debit, Decimal("0.00"))
        self.assertEqual(self._payable_net(), Decimal("45000.00"))

    def test_pay_without_accrual_returns_400(self):
        self.run.status = "finalised"
        self.run.save(update_fields=["status"])
        res = self.client.post(
            f"/api/hr/payroll-runs/{self.run.id}/pay/",
            {"payment_account_id": str(self.bank.id)},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertIn("accrual", str(res.data).lower())
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "finalised")
        self.assertFalse(
            GeneralLedgerEntry.objects.filter(
                created_by=self.tenant, reference="PAYROLL-PAID-2026-09"
            ).exists()
        )

    def test_approve_leaves_salaries_payable_as_credit(self):
        self.run.status = "calculated"
        self.run.save(update_fields=["status"])
        res = self.client.post(f"/api/hr/payroll-runs/{self.run.id}/approve/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(self._payable_net(), Decimal("45000.00"))

    def test_pay_after_approve_clears_salaries_payable(self):
        self.run.status = "calculated"
        self.run.save(update_fields=["status"])
        approve = self.client.post(f"/api/hr/payroll-runs/{self.run.id}/approve/")
        self.assertEqual(approve.status_code, status.HTTP_200_OK, approve.data)
        self.assertEqual(self._payable_net(), Decimal("45000.00"))

        pay = self.client.post(
            f"/api/hr/payroll-runs/{self.run.id}/pay/",
            {"payment_account_id": str(self.bank.id)},
            format="json",
        )
        self.assertEqual(pay.status_code, status.HTTP_200_OK, pay.data)
        self.assertEqual(self._payable_net(), Decimal("0.00"))

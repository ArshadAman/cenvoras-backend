import datetime
import os
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hr.services.hr_accounting_service import HRAccountingService
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService
from users.models import User

AMT = Decimal('277608.00')


class CorrectPayrollCashPostingTests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='corr', email='corr@ex.com', password='x')
        acc = AccountingService.get_or_create_default_accounts(self.t)
        self.cash, self.bank = acc['1001'], acc['1002']
        self.sp = HRAccountingService.get_or_create_hr_accounts(self.t)['2250']
        d = datetime.date(2026, 9, 22)
        GeneralLedgerEntry.objects.create(date=d, account=self.sp, debit=AMT, credit=0, description='Disbursement', reference='PAYROLL-PAID-2026-09', created_by=self.t)
        GeneralLedgerEntry.objects.create(date=d, account=self.cash, debit=0, credit=AMT, description='Salary disbursement for September 2026 from Cash', reference='PAYROLL-PAID-2026-09', created_by=self.t)
        GeneralLedgerEntry.objects.create(date=d, account=self.cash, debit=Decimal('10199.99'), credit=0, description='other', reference='RCPT-1', created_by=self.t)

    def run_cmd(self, *extra):
        out = StringIO()
        call_command('correct_payroll_cash_posting', '--tenant', 'corr', '--year', '2026', '--month', '9', *extra, stdout=out)
        return out.getvalue()

    def test_check_only_explains_balance(self):
        out = self.run_cmd('--check-only')
        self.assertIn('-277,608.00', out)
        self.assertIn('-267,408.01', out)
        self.assertEqual(GeneralLedgerEntry.objects.count(), 3)

    def test_bank_date_required(self):
        with self.assertRaises(CommandError):
            self.run_cmd()

    def test_dry_run_writes_nothing(self):
        out = self.run_cmd('--bank-date', '2026-10-25')
        self.assertIn('DRY-RUN', out)
        self.assertEqual(GeneralLedgerEntry.objects.count(), 3)

    def test_apply_requires_env_guard(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CENVORA_ALLOW_LEDGER_CORRECTION', None)
            with self.assertRaises(CommandError):
                self.run_cmd('--bank-date', '2026-10-25', '--apply')
        self.assertEqual(GeneralLedgerEntry.objects.count(), 3)

    @patch.dict(os.environ, {'CENVORA_ALLOW_LEDGER_CORRECTION': 'yes'})
    def test_apply_reverses_exactly_reposts_to_bank_and_is_idempotent(self):
        originals = set(GeneralLedgerEntry.objects.filter(reference='PAYROLL-PAID-2026-09').values_list('id', 'debit', 'credit', 'date'))
        self.run_cmd('--bank-date', '2026-10-25', '--apply')
        rev = GeneralLedgerEntry.objects.filter(reference='PAYROLL-PAID-2026-09-REV')
        self.assertEqual(rev.get(account=self.cash).debit, AMT)
        self.assertEqual(rev.get(account=self.sp).credit, AMT)
        self.assertTrue(all(e.date == datetime.date(2026, 9, 22) for e in rev))
        r1 = GeneralLedgerEntry.objects.filter(reference='PAYROLL-PAID-2026-09-R1')
        self.assertEqual(r1.get(account=self.sp).debit, AMT)
        self.assertEqual(r1.get(account=self.bank).credit, AMT)
        self.assertTrue(all(e.date == datetime.date(2026, 10, 25) for e in r1))
        # original untouched
        self.assertEqual(originals, set(GeneralLedgerEntry.objects.filter(reference='PAYROLL-PAID-2026-09').values_list('id', 'debit', 'credit', 'date')))
        cash_bal = sum(e.debit - e.credit for e in GeneralLedgerEntry.objects.filter(account=self.cash))
        self.assertEqual(cash_bal, Decimal('10199.99'))
        n = GeneralLedgerEntry.objects.count()
        self.run_cmd('--bank-date', '2026-10-25', '--apply')
        self.assertEqual(GeneralLedgerEntry.objects.count(), n)

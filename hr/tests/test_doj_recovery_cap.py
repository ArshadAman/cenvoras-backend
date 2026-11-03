"""Rudra follow-up to #205: earlier-FY DOJ recovery capped per month, carried forward, net never < 0."""
import datetime
from decimal import Decimal

from hr.models import DOJCorrection, EmployeeSalaryAssignment, HRMSSettings, PayrollRun, Payslip
from hr.serializers import PayslipSerializer
from hr.services.doj_correction import apply_doj_correction, cap_prior_fy_recovery
from hr.tests.test_doj_correction_rudra_review import _Base

D = Decimal


def rec(ps):
    return sum((D(v) for k, v in ps.deductions.items() if k.startswith('DOJ correction recovery, earlier FY')),
               D('0.00'))


class DOJRecoveryCapTests(_Base):
    def prior_fy_recovery(self, cap=None):
        """Excess paid for all of March 2026 (FY 25-26); DOJ moved to 16-Mar → recovery in April 2026."""
        self.emp.date_of_joining = datetime.date(2026, 3, 1)
        self.emp.save(update_fields=['date_of_joining'])
        Payslip.objects.all().delete()
        PayrollRun.objects.all().delete()
        EmployeeSalaryAssignment.objects.filter(employee=self.emp).update(effective_from='2026-01-01')
        run = PayrollRun.objects.create(tenant=self.tenant, month=3, year=2026, status='locked')
        Payslip.objects.create(tenant=self.tenant, payroll_run=run, employee=self.emp, total_working_days=26,
                               present_days=26, gross_salary=D('26000'), net_salary=D('26000'))
        if cap is not None:
            s, _ = HRMSSettings.objects.get_or_create(tenant=self.tenant)
            s.doj_recovery_cap_percent = D(cap)
            s.save()
        return apply_doj_correction(self.emp, datetime.date(2026, 3, 16), 'Joined mid-March', self.tenant)

    def test_default_cap_is_50_percent(self):
        self.assertEqual(HRMSSettings._meta.get_field('doj_recovery_cap_percent').default, D('50.00'))

    def test_cap_binds_and_balance_on_payslip(self):
        c = self.prior_fy_recovery(cap='20')
        total = -c.amount
        ps = self.slip(4, 2026)
        self.assertEqual(rec(ps), D('5200.00'))  # 20% of 26,000
        self.assertEqual(ps.doj_recovery_balance, total - D('5200.00'))
        ps.save()
        self.assertEqual(D(PayslipSerializer(ps).data['doj_recovery_balance']), total - D('5200.00'))
        carry = DOJCorrection.objects.get(carried_from__isnull=False, target_month=5)
        self.assertEqual(carry.amount, -(total - D('5200.00')))
        self.slip(4, 2026)  # recompute: no duplicate carry
        self.assertEqual(DOJCorrection.objects.filter(carried_from__isnull=False, target_month=5).count(), 1)

    def test_carry_forward_over_several_months(self):
        c = self.prior_fy_recovery(cap='20')
        remaining = -c.amount
        recovered = D('0.00')
        month = 4
        while remaining > 0:
            ps = self.slip(month, 2026)
            r = rec(ps)
            self.assertLessEqual(r, D('5200.00'))
            self.assertGreater(r, 0)
            recovered += r
            remaining -= r
            self.assertEqual(ps.doj_recovery_balance, remaining)
            self.assertGreaterEqual(ps.net_salary, 0)
            month += 1
            self.assertLess(month, 12)
        self.assertGreater(month, 5)  # needed more than one month
        self.assertEqual(recovered, -c.amount)
        self.assertEqual(rec(self.slip(month, 2026)), D('0.00'))  # nothing left

    def test_net_pay_never_below_zero(self):
        self.prior_fy_recovery(cap='100')
        ps = self.slip(4, 2026, days='2')  # 2,000 gross, recovery is much larger
        self.assertGreaterEqual(ps.net_salary, D('0.00'))
        self.assertGreater(ps.doj_recovery_balance, 0)

    def test_cap_helper_limits_to_available_pay(self):
        lines = [('a', D('9000')), ('b', D('3000'))]
        applied, got, bal = cap_prior_fy_recovery(lines, D('10000'), D('1500'), D('50'))
        self.assertEqual((got, bal, applied), (D('1500.00'), D('10500.00'), [('a', D('1500.00'))]))
        applied, got, bal = cap_prior_fy_recovery(lines, D('10000'), D('-50'), D('50'))
        self.assertEqual((got, bal, applied), (D('0.00'), D('12000.00'), []))

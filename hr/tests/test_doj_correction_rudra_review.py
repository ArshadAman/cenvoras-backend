"""UAT P1 #10, Rudra CA review: FY split of recoveries, floor + carry, PF arrears, warnings, ESIC flag."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

import hr.services.payroll_engine as engine
from hr.models import DOJCorrection, EmployeeSalaryAssignment, PayrollRun, Payslip
from hr.services.doj_correction import (
    ESIC_FLAG, RECOVERY_PF_WARNING, apply_doj_correction, arrears_pf,
)
from hr.tests.test_doj_correction_uat10 import DOJCorrectionFlowTests

D = Decimal


def bd(days):
    lop = D('26') - D(days)
    return {'present_days': D(days), 'payable_days': D(days), 'paid_leave_days': D('0'),
            'absent_days': lop, 'lop_days': lop, 'days_before_joining': 0,
            'absent_dates': [], 'unpaid_leave_dates': []}


class _Base(DOJCorrectionFlowTests):
    __test__ = True
    """Reuses the flow fixture (Odisha employee, ₹26,000 = Basic 13,000 + HRA 13,000; Aug+Sep 2026 run)."""

    def slip(self, month, year, days='26'):
        run, _ = PayrollRun.objects.get_or_create(tenant=self.tenant, month=month, year=year,
                                                  defaults={'status': 'draft'})
        with patch('hr.services.payroll_engine.get_attendance_breakdown', return_value=bd(days)), \
             patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
            return engine.compute_payslip_for_employee(self.emp, run)


class RecoveryFYTests(_Base):
    def test_same_fy_recovery_is_negative_earning_with_pf_warning(self):
        apply_doj_correction(self.emp, datetime.date(2026, 8, 16), 'Offer letter', self.tenant)
        ps = self.slip(10, 2026)
        self.assertEqual(ps.gross_salary, D('13000.00'))
        self.assertFalse(any('earlier FY' in k for k in ps.deductions))
        self.assertIn(RECOVERY_PF_WARNING, ps.deduction_reasons['DOJ correction']['warnings'])

    def test_earlier_fy_recovery_is_post_tax_deduction_and_flagged(self):
        # Excess was paid in March 2026 (FY 2025-26); recovered in the April 2026 run (FY 2026-27).
        self.emp.date_of_joining = datetime.date(2026, 3, 1)
        self.emp.save(update_fields=['date_of_joining'])
        Payslip.objects.all().delete()
        PayrollRun.objects.all().delete()
        EmployeeSalaryAssignment.objects.filter(employee=self.emp).update(effective_from='2026-01-01')
        run = PayrollRun.objects.create(tenant=self.tenant, month=3, year=2026, status='locked')
        Payslip.objects.create(tenant=self.tenant, payroll_run=run, employee=self.emp, total_working_days=26,
                               present_days=26, gross_salary=D('26000'), net_salary=D('26000'))
        c = apply_doj_correction(self.emp, datetime.date(2026, 3, 16), 'Joined mid-March', self.tenant)
        self.assertEqual((c.target_month, c.target_year), (4, 2026))
        ps = self.slip(4, 2026)
        self.assertEqual(ps.gross_salary, D('26000.00'))  # taxable gross untouched
        key = [k for k in ps.deductions if k.startswith('DOJ correction recovery, earlier FY')]
        self.assertEqual(len(key), 1)
        self.assertEqual(D(ps.deductions[key[0]]), -c.amount)
        self.assertGreaterEqual(ps.total_deductions, -c.amount)
        w = ps.deduction_reasons['DOJ correction']['warnings']
        self.assertIn(RECOVERY_PF_WARNING, w)
        self.assertTrue(any('earlier FY' in x for x in w))

    def test_floor_at_zero_and_carry_excess_idempotently(self):
        apply_doj_correction(self.emp, datetime.date(2026, 8, 16), 'Offer letter', self.tenant)  # −13,000
        ps = self.slip(10, 2026, days='5')  # Oct pay 26,000 × 5/26 = 5,000
        self.assertEqual(ps.gross_salary, D('0.00'))
        carry = DOJCorrection.objects.get(carried_from__isnull=False)
        self.assertEqual((carry.target_month, carry.target_year, carry.amount), (11, 2026, D('-8000.00')))
        self.slip(10, 2026, days='5')  # recalculating the same run doesn't duplicate the carry
        self.assertEqual(DOJCorrection.objects.filter(carried_from__isnull=False).count(), 1)
        nov = self.slip(11, 2026)
        self.assertEqual(nov.gross_salary, D('18000.00'))  # 26,000 − 8,000 carried


class PFArrearsTests(_Base):
    def test_pf_wage_capped_at_related_month_ceiling(self):
        lines = [{'pf_wage_delta': '8000', 'paid_pf_wage': '12000'},  # only 3,000 room under ₹15k
                 {'pf_wage_delta': '5000', 'paid_pf_wage': '0'}]       # fully in
        pf = arrears_pf(lines, engine.get_hrms_settings(self.tenant))
        self.assertEqual(pf['wages'], D('8000.00'))
        self.assertEqual(pf['employee_pf'], D('960.00'))  # 12% of 8,000

    def test_arrears_pf_kept_separate_and_esic_flagged(self):
        PayrollRun.objects.create(tenant=self.tenant, month=7, year=2026, status='locked')
        with patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
            c = apply_doj_correction(self.emp, datetime.date(2026, 7, 20), 'Joined earlier', self.tenant)
        self.assertEqual(c.amount, D('10000.00'))
        ps = self.slip(10, 2026)
        # PF wage part of the arrears = Basic 13,000 × 10/26 = 5,000 → EE PF 600, separate from current wages
        self.assertEqual(ps.arrear_pf_wages, D('5000.00'))
        self.assertEqual(ps.arrear_employee_pf, D('600.00'))
        self.assertEqual(ps.employee_pf - ps.arrear_employee_pf,
                         engine.compute_pf(D('13000.00'), engine.get_hrms_settings(self.tenant))['employee_pf'])
        self.assertTrue(any(ESIC_FLAG in k for k in ps.earnings))
        self.assertIn('PF (arrears)', ps.deduction_reasons)
        # Locked months are never recalculated
        self.assertEqual(Payslip.objects.filter(payroll_run__month__in=(8, 9)).count(), 2)
        self.assertEqual(set(Payslip.objects.values_list('gross_salary', flat=True)), {D('26000.00')})

"""EPF Act s.6 (Rudra CA): PF wage = Basic + DA (flagged), capped at pf_wage_ceiling()."""
import datetime
from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import (Department, Designation, Employee, EmployeeSalaryAssignment, PayrollRun, Payslip,
                       SalaryComponent, SalaryStructure)
from hr.services.payroll_engine import pf_wage_ceiling, run_payroll
from users.models import User


class PfWageDaTests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='da', email='da@x.in', password='pw', role='admin')
        self.d = Department.objects.create(tenant=self.t, name='D')
        self.g = Designation.objects.create(tenant=self.t, name='G')
        self.st = SalaryStructure.objects.create(tenant=self.t, name='S')
        SalaryComponent.objects.create(salary_structure=self.st, name='Basic', component_type='fixed', value='0',
                                       is_basic=True)
        self.da = SalaryComponent.objects.create(salary_structure=self.st, name='DA', component_type='fixed',
                                                 value='0', is_dearness_allowance=True)
        self.api = APIClient()
        self.api.force_authenticate(self.t)

    def emp(self, code, ctc, comps, confirmed=False):
        e = Employee.objects.create(
            tenant=self.t, employee_code=code, full_name=f'Emp {code}', date_of_birth='1990-01-01',
            date_of_joining=datetime.date(2026, 1, 1), gender='M', employment_type='full_time',
            designation=self.g, department=self.d, status='active', work_state='Karnataka',
            bank_account_number='1', bank_ifsc='SBIN0001234')
        a = EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=e, salary_structure=self.st,
                                                    effective_from='2026-01-01', monthly_ctc=D(ctc),
                                                    computed_components=comps)
        if confirmed:
            a.ctc_scaling_confirmation = {'monthly_ctc': str(D(ctc)), 'components': {k: str(v) for k, v in comps.items()}}
            a.save()
        return e

    def run_month(self):
        r = PayrollRun.objects.create(tenant=self.t, month=9, year=2026, status='draft')
        run_payroll(str(r.id))
        return r

    def slip(self, r, e):
        return Payslip.objects.get(payroll_run=r, employee=e)

    def test_ceiling_helper_is_15000_for_now(self):
        self.assertEqual(pf_wage_ceiling(datetime.date(2026, 10, 31)), D('15000.00'))

    def test_basic_plus_da_under_cap(self):
        e = self.emp('U1', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        ps = self.slip(self.run_month(), e)
        self.assertEqual(ps.deduction_reasons['PF']['pf_wage'], '10000.00')
        self.assertEqual(ps.employee_pf, D('1200.00'))

    def test_da_not_counted_unless_flagged(self):
        self.da.is_dearness_allowance = False
        self.da.save()
        e = self.emp('U2', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        self.assertEqual(self.slip(self.run_month(), e).employee_pf, D('960.00'))

    def test_basic_plus_da_capped(self):
        e = self.emp('C1', '40000', {'Basic': '12000', 'DA': '6000', 'Special Allowance': '22000'})
        ps = self.slip(self.run_month(), e)
        self.assertEqual(ps.deduction_reasons['PF']['pf_wage'], '18000.00')
        self.assertEqual(ps.employee_pf, D('1800.00'))  # 12% of the ₹15,000 ceiling

    def test_confirmed_scaled_basic_plus_da(self):
        # Basic+DA+HRA = 30,000 > CTC 20,000 → scaled ×2/3 → Basic 8,000 + DA 2,000.
        e = self.emp('S1', '20000', {'Basic': '12000', 'DA': '3000', 'HRA': '15000'}, confirmed=True)
        ps = self.slip(self.run_month(), e)
        self.assertEqual(ps.gross_salary, D('20000.00'))
        self.assertEqual(ps.deduction_reasons['PF']['pf_wage'], '10000.00')
        self.assertEqual(ps.employee_pf, D('1200.00'))

    def test_preview_flags_pf_wage_change(self):
        self.da.is_dearness_allowance = False
        self.da.save()
        e = self.emp('P1', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        r = self.run_month()
        self.da.is_dearness_allowance = True
        self.da.save()
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/recalculate-preview/')
        row = [c for c in resp.data['changes'] if c['employee_code'] == 'P1'][0]
        self.assertTrue(row['pf_wage_changed'])
        self.assertEqual((row['old_pf_wage'], row['new_pf_wage']), ('8000.00', '10000.00'))
        self.assertFalse(row['gross_changed'])
        self.assertEqual(self.slip(r, e).employee_pf, D('960.00'))  # preview saved nothing

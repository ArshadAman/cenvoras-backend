"""Lovelish PF follow-ups: stored PF wage vs capped contribution base; custom allowances as PF wages."""
from decimal import Decimal as D

from hr.models import EmployeeSalaryAssignment, Payslip, SalaryStructure, SalaryComponent
from hr.tests.test_pf_wage_da import PfWageDaTests as _Base


class PfBaseAndCustomTests(_Base):
    test_ceiling_helper_is_15000_for_now = test_basic_plus_da_under_cap = None
    test_da_not_counted_unless_flagged = test_basic_plus_da_capped = None
    test_confirmed_scaled_basic_plus_da = test_preview_flags_pf_wage_change = None

    def test_stores_statutory_wage_and_capped_base_and_note(self):
        e = self.emp('C1', '40000', {'Basic': '15000', 'DA': '2000', 'Special Allowance': '23000'})
        pf = self.slip(self.run_month(), e).deduction_reasons['PF']
        self.assertEqual((pf['pf_wage'], pf['pf_contribution_base']), ('17000.00', '15000.00'))
        self.assertIn('PF wage ₹17,000 (capped at ₹15,000)', pf['reason'])

    def test_preview_ignores_representation_change(self):
        e = self.emp('H1', '150000', {'Basic': '75000', 'Special Allowance': '75000'})
        r = self.run_month()
        ps = self.slip(r, e)
        # Payslip saved before this fix: no contribution base, uncapped pf_wage only.
        reasons = dict(ps.deduction_reasons)
        reasons['PF'] = {k: v for k, v in reasons['PF'].items() if k != 'pf_contribution_base'}
        Payslip.objects.filter(pk=ps.pk).update(deduction_reasons=reasons)
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/recalculate-preview/')
        self.assertFalse([c for c in resp.data['changes'] if c['employee_code'] == 'H1' and c['pf_wage_changed']])

    def test_custom_allowance_flag(self):
        self.da.delete()  # no structure 'DA': 'DA' below is an employee-level custom allowance
        e = self.emp('X1', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        self.assertEqual(self.slip(self.run_month(), e).employee_pf, D('960.00'))  # not counted by default
        resp = self.api.get('/api/hr/pf-wage-components/')
        m = [x for x in resp.data['matches'] if x['kind'] == 'custom']
        self.assertEqual([x['name'] for x in m], ['DA'])
        self.assertTrue(resp.data['show_banner'])
        r2 = self.api.post('/api/hr/pf-wage-components/',
                           {'action': 'flag', 'assignment_id': m[0]['assignment_id'], 'name': 'DA'}, format='json')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(EmployeeSalaryAssignment.objects.get(employee=e).computed_components['_pf_wages'], ['DA'])
        from hr.models import PayrollRun
        PayrollRun.objects.all().delete()
        self.assertEqual(self.slip(self.run_month(), e).employee_pf, D('1200.00'))
        self.assertFalse([x for x in self.api.get('/api/hr/pf-wage-components/').data['matches']
                          if x['kind'] == 'custom'])


import datetime as _dt
from hr.services.payroll_engine import _payslip_pf_wage, compute_pf, pf_wage_ceiling

# (wage-month date, Basic, DA, expected employee PF at the CURRENT ceiling helper).
# When the ₹25k ceiling is approved for months from 17-09-2026, change the second row's expectation to
# 2040.00 (12% of 17,000) — the test takes the date and asks pf_wage_ceiling(date), it hardcodes no ceiling.
PF_CASES = [
    (_dt.date(2026, 10, 31), '8000', '2000', '1200.00'),
    (_dt.date(2026, 10, 31), '15000', '2000', '1800.00'),
]


class PfCeilingByWageMonthTests(_Base):
    test_ceiling_helper_is_15000_for_now = test_basic_plus_da_under_cap = None
    test_da_not_counted_unless_flagged = test_basic_plus_da_capped = None
    test_confirmed_scaled_basic_plus_da = test_preview_flags_pf_wage_change = None

    def test_cases(self):
        for on, basic, da, expected in PF_CASES:
            with self.subTest(on=on, basic=basic, da=da):
                wage = D(basic) + D(da)
                pf = compute_pf(wage, None, on_date=on)
                self.assertEqual(pf['employee_pf'], D(expected))
                self.assertEqual(pf['pf_contribution_base'], min(wage, pf_wage_ceiling(on)))

    def test_vpf_employee_derived_base(self):
        # 'VPF' is a custom deduction component, never part of employee_pf; arrears PF is excluded too.
        SalaryComponent.objects.create(salary_structure=self.st, name='VPF', type='deduction',
                                       component_type='fixed', value='0')
        e = self.emp('V1', '40000', {'Basic': '20000', 'Special Allowance': '20000', 'VPF': '3000'})
        r = self.run_month()
        ps = self.slip(r, e)
        self.assertEqual(ps.employee_pf, D('1800.00'))
        reasons = dict(ps.deduction_reasons)
        reasons['PF'] = {k: v for k, v in reasons['PF'].items() if k != 'pf_contribution_base'}
        Payslip.objects.filter(pk=ps.pk).update(deduction_reasons=reasons, employee_pf=D('2100.00'),
                                                arrear_employee_pf=D('300.00'))
        ps.refresh_from_db()
        self.assertEqual(_payslip_pf_wage(ps), (D('15000.00'), 'derived'))
        # Even if a voluntary extra had been folded into employee_pf, the derived base stays at the ceiling.
        Payslip.objects.filter(pk=ps.pk).update(employee_pf=D('5000.00'), arrear_employee_pf=0)
        ps.refresh_from_db()
        self.assertEqual(_payslip_pf_wage(ps), (pf_wage_ceiling(_dt.date(2026, 9, 30)), 'derived'))
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/recalculate-preview/')
        self.assertEqual(resp.status_code, 200)

    def test_same_name_custom_row_not_in_banner(self):
        # 'DA' is a structure component (flag on/off there), so it is never listed as a custom allowance.
        self.da.counts_as_pf_wages = False
        self.da.save()
        self.emp('N1', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        m = self.api.get('/api/hr/pf-wage-components/').data['matches']
        self.assertFalse([x for x in m if x['kind'] == 'custom'])
        self.assertTrue([x for x in m if x['kind'] == 'structure' and x['name'] == 'DA'])


class CustomAllowanceSingleSourceTests(_Base):
    test_ceiling_helper_is_15000_for_now = test_basic_plus_da_under_cap = None
    test_da_not_counted_unless_flagged = test_basic_plus_da_capped = None
    test_confirmed_scaled_basic_plus_da = test_preview_flags_pf_wage_change = None

    def setUp(self):
        super().setUp()
        self.da.delete()

    def _custom(self):
        return [x for x in self.api.get('/api/hr/pf-wage-components/').data['matches'] if x['kind'] == 'custom']

    def test_zero_or_superseded_da_not_listed(self):
        e = self.emp('Z1', '20000', {'Basic': '10000', 'HRA': '5000', 'DA': '0.00', 'da': '0',
                                     'Special Allowance': '5000'})
        self.assertEqual(self._custom(), [])
        # Older assignment had a real DA; the current one (latest effective date) has none.
        EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=e, salary_structure=self.st,
                                                effective_from='2025-01-01', monthly_ctc=D('20000'),
                                                computed_components={'Basic': '8000', 'DA': '2000'})
        self.assertEqual(self._custom(), [])
        r = self.api.get(f'/api/hr/employees/{e.id}/')
        self.assertEqual(r.data['salary_details']['custom_allowances'], {})

    def test_banner_and_form_agree(self):
        e = self.emp('A1', '20000', {'Basic': '8000', 'DA': '2000', 'Special Allowance': '10000'})
        self.assertEqual([x['name'] for x in self._custom()], ['DA'])
        r = self.api.get(f'/api/hr/employees/{e.id}/')
        self.assertEqual(r.data['salary_details']['custom_allowances'], {'DA': '2000.00'})

    def test_remove_custom_allowance_on_save(self):
        e = self.emp('R1', '30000', {'Basic': '15000', 'HRA': '7500', 'Retaining Allowance': '1000',
                                     'Special Allowance': '6500', '_basic_pct': '50.00', '_hra_pct': '50.00'})
        r = self.api.patch(f'/api/hr/employees/{e.id}/', {'salary': {
            'monthly_ctc': 30000, 'basic_pct': 50, 'hra_pct': 50,
            'components': {'Basic': 15000, 'HRA': 7500}}}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        a = EmployeeSalaryAssignment.objects.get(employee=e)
        self.assertNotIn('Retaining Allowance', a.computed_components)
        self.assertEqual(self.api.get(f'/api/hr/employees/{e.id}/').data['salary_details']['custom_allowances'], {})

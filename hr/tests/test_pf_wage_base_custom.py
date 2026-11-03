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

"""Rudra CA (#227): no silent scaling, approval block, admin confirm, PF on scaled Basic, recalc preview."""
import datetime
from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import (Department, Designation, Employee, EmployeeSalaryAssignment, PayrollException,
                       PayrollRun, Payslip, SalaryComponent, SalaryStructure)
from hr.services.payroll_engine import VERIFY_SALARY_STRUCTURE, run_payroll
from users.models import User


class CtcScalingFlowTests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='cs', email='cs@x.in', password='pw', role='admin')
        self.d = Department.objects.create(tenant=self.t, name='D')
        self.g = Designation.objects.create(tenant=self.t, name='G')
        self.st = SalaryStructure.objects.create(tenant=self.t, name='S')
        SalaryComponent.objects.create(salary_structure=self.st, name='Basic', component_type='fixed', value='0',
                                       is_basic=True)
        self.api = APIClient()
        self.api.force_authenticate(self.t)

    def emp(self, code, ctc, comps):
        e = Employee.objects.create(
            tenant=self.t, employee_code=code, full_name=f'Emp {code}', date_of_birth='1990-01-01',
            date_of_joining=datetime.date(2026, 1, 1), gender='M', employment_type='full_time',
            designation=self.g, department=self.d, status='active', work_state='Karnataka',
            bank_account_number='123456789', bank_ifsc='SBIN0001234')
        a = EmployeeSalaryAssignment.objects.create(
            tenant=self.t, employee=e, salary_structure=self.st, effective_from='2026-01-01',
            monthly_ctc=D(ctc), computed_components=comps)
        return e, a

    def run_month(self, month=9):
        r = PayrollRun.objects.create(tenant=self.t, month=month, year=2026, status='draft')
        run_payroll(str(r.id))
        r.refresh_from_db()
        return r

    def test_flag_blocks_approval_and_confirm_scales(self):
        e, a = self.emp('X1', '20000', {'Basic': '15000', 'HRA': '15000'})
        r = self.run_month()
        ps = Payslip.objects.get(payroll_run=r, employee=e)
        self.assertIn(VERIFY_SALARY_STRUCTURE, ps.deduction_reasons)
        self.assertEqual(ps.gross_salary, D('30000.00'))  # unscaled until confirmed
        self.assertEqual(ps.deduction_reasons[VERIFY_SALARY_STRUCTURE]['scaled_components']['Basic'], '10000.00')
        exc = PayrollException.objects.get(payroll_run=r, code='SALARY_EXCEEDS_CTC')
        self.assertEqual(exc.severity, 'critical')
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/approve/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Emp X1 (X1)', str(resp.data))
        # generic resolve can't dismiss it
        self.assertEqual(self.api.post(f'/api/hr/exceptions/{exc.id}/resolve/').status_code, 400)
        # confirm needs a reason
        url = f'/api/hr/payroll-runs/{r.id}/confirm-ctc-scaling/'
        self.assertEqual(self.api.post(url, {'employee_id': str(e.id)}, format='json').status_code, 400)
        resp = self.api.post(url, {'employee_id': str(e.id), 'reason': 'Offer letter CTC is correct'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        a.refresh_from_db()
        self.assertEqual(a.ctc_scaling_confirmation['reason'], 'Offer letter CTC is correct')
        self.assertEqual(a.ctc_scaling_confirmation['by_email'], 'cs@x.in')
        ps = Payslip.objects.get(payroll_run=r, employee=e)
        self.assertNotIn(VERIFY_SALARY_STRUCTURE, ps.deduction_reasons)
        self.assertEqual(ps.gross_salary, D('20000.00'))
        self.assertEqual(ps.employee_pf, D('1200.00'))  # 12% of scaled Basic 10,000
        self.assertFalse(PayrollException.objects.filter(payroll_run=r, code='SALARY_EXCEEDS_CTC').exists())

    def test_confirmation_invalidated_when_components_change(self):
        from hr.services.payroll_engine import scaling_confirmed
        e, a = self.emp('X2', '20000', {'Basic': '15000', 'HRA': '15000'})
        a.ctc_scaling_confirmation = {'monthly_ctc': '20000', 'components': {'Basic': '15000', 'HRA': '15000'}}
        self.assertTrue(scaling_confirmed(a))
        a.computed_components = {'Basic': '16000', 'HRA': '15000'}
        self.assertFalse(scaling_confirmed(a))

    def test_pf_on_scaled_basic_capped_at_ceiling(self):
        e, a = self.emp('X3', '60000', {'Basic': '40000', 'HRA': '40000'})
        a.ctc_scaling_confirmation = {'monthly_ctc': '60000.00',
                                      'components': {'Basic': '40000', 'HRA': '40000'}}
        a.save()
        r = self.run_month()
        ps = Payslip.objects.get(payroll_run=r, employee=e)
        self.assertEqual(ps.gross_salary, D('60000.00'))
        self.assertEqual(ps.employee_pf, D('1800.00'))  # scaled Basic 30,000 → ₹15,000 ceiling × 12%

    def test_recalculate_preview_diff_saves_nothing(self):
        e, a = self.emp('X4', '20000', {'Basic': '10000', 'HRA': '5000'})
        r = self.run_month()
        old = Payslip.objects.get(payroll_run=r, employee=e)
        self.assertEqual(old.gross_salary, D('20000.00'))
        a.monthly_ctc = D('25000')
        a.save()
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/recalculate-preview/')
        self.assertEqual(resp.status_code, 200, resp.data)
        row = [c for c in resp.data['changes'] if c['employee_code'] == 'X4'][0]
        self.assertEqual((row['old_gross'], row['new_gross']), ('20000.00', '25000.00'))
        self.assertFalse(row['new_gross_within_esi_limit'])
        if D(row['old_employee_esi']) > 0:
            self.assertEqual(row['esi_change'], 'no longer ESI-covered')
        self.assertIn('pt_changed', row)
        # nothing saved
        self.assertEqual(Payslip.objects.get(pk=old.pk).gross_salary, D('20000.00'))
        self.assertEqual(Payslip.objects.filter(payroll_run=r).count(), 1)

    def test_preview_blocked_for_approved_run(self):
        self.emp('X5', '20000', {'Basic': '10000'})
        r = self.run_month()
        r.status = 'approved'
        r.save()
        self.assertEqual(self.api.post(f'/api/hr/payroll-runs/{r.id}/recalculate-preview/').status_code, 400)


class FinaliseGuardTests(CtcScalingFlowTests):
    """Rudra CA: finalise is guarded exactly like approve."""

    def _pan_run(self):
        e, _a = self.emp('P1', '20000', {'Basic': '10000', 'HRA': '5000'})
        r = self.run_month()
        PayrollException.objects.filter(payroll_run=r).delete()
        PayrollException.objects.create(tenant=self.t, payroll_run=r, employee=e, severity='critical',
                                        code='PAN_REQUIRED_FOR_TDS', message='PAN is required: TDS is deducted.')
        return r

    def test_finalise_blocked_by_verify_flag(self):
        self.emp('X1', '20000', {'Basic': '15000', 'HRA': '15000'})
        r = self.run_month()
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/finalise/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Emp X1 (X1)', str(resp.data))
        self.assertIn('Cannot finalise payroll', str(resp.data))
        self.assertNotIn('Cannot approve', str(resp.data))
        r.refresh_from_db()
        self.assertEqual(r.status, 'calculated')

    def test_finalise_blocked_by_pan_tds_exception(self):
        r = self._pan_run()
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/finalise/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Emp P1 (P1)', str(resp.data))
        self.assertIn('Cannot finalise payroll', str(resp.data))
        self.assertNotIn('..', str(resp.data))
        r.refresh_from_db()
        self.assertEqual(r.status, 'calculated')

    def test_approve_blocked_by_pan_tds_exception(self):
        r = self._pan_run()
        resp = self.api.post(f'/api/hr/payroll-runs/{r.id}/approve/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Emp P1 (P1)', str(resp.data))
        r.refresh_from_db()
        self.assertEqual(r.status, 'calculated')

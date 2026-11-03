"""UAT R2 #10: DOJ correction arrears when the month had no payslip / no run; earliest open target."""
import datetime
from decimal import Decimal as D

from django.test import TestCase

from hr.models import (
    Department, Designation, DOJCorrection, Employee, EmployeeSalaryAssignment, PayrollRun, Payslip,
    SalaryComponent, SalaryStructure,
)
from hr.services.doj_correction import (
    ARREARS_PF_WARNING, ESIC_FLAG, RECOVERY_PF_WARNING, apply_doj_correction, compute_correction,
    correction_warnings, next_open_month,
)
from users.models import User


class R12Tests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='r12', email='r12@x.in', password='pw', role='admin')
        dept = Department.objects.create(tenant=self.t, name='Ops')
        des = Designation.objects.create(tenant=self.t, name='Exec')
        st = SalaryStructure.objects.create(tenant=self.t, name='S')
        SalaryComponent.objects.create(salary_structure=st, name='Basic', component_type='fixed',
                                       value='13000.00', is_basic=True)
        SalaryComponent.objects.create(salary_structure=st, name='HRA', component_type='fixed',
                                       value='13000.00', is_basic=False)
        self.emp = Employee.objects.create(
            tenant=self.t, employee_code='R12', full_name='R12 MidYear', date_of_birth='1990-01-01',
            date_of_joining=datetime.date(2026, 10, 1), gender='M', employment_type='full_time',
            designation=des, department=dept, status='active', work_state='Odisha')
        # As on the demo: salary assignment starts on the OLD DOJ.
        EmployeeSalaryAssignment.objects.create(
            tenant=self.t, employee=self.emp, salary_structure=st, effective_from='2026-10-01',
            monthly_ctc=D('26000.00'), computed_components={'Basic': '13000.00', 'HRA': '13000.00'})
        self.sep = PayrollRun.objects.create(tenant=self.t, month=9, year=2026, status='approved')
        octr = PayrollRun.objects.create(tenant=self.t, month=10, year=2026, status='approved')
        Payslip.objects.create(tenant=self.t, payroll_run=octr, employee=self.emp, total_working_days=27,
                               present_days=27, gross_salary=D('26000'), net_salary=D('26000'))
        self.nov = PayrollRun.objects.create(tenant=self.t, month=11, year=2026, status='calculated')

    def test_target_is_earliest_open_run(self):
        self.assertEqual(next_open_month(self.t), (2026, 11))

    def test_full_month_arrears_without_payslip(self):
        amount, lines = compute_correction(self.emp, datetime.date(2026, 9, 1))
        self.assertEqual(len(lines), 1)
        ln = lines[0]
        self.assertEqual((ln['year'], ln['month'], ln['old_payable_days']), (2026, 9, 0))
        self.assertEqual(ln['new_payable_days'], ln['working_days'])
        self.assertEqual(amount, D('26000.00'))
        self.assertFalse(ln['had_payslip'])

    def test_partial_month(self):
        # Sep 2026: Tue 15 Sep → 1–14 has Sundays 6, 13 → 12 pre-join days.
        amount, lines = compute_correction(self.emp, datetime.date(2026, 9, 15))
        w = lines[0]['working_days']
        self.assertEqual(lines[0]['new_payable_days'], w - 12)
        self.assertEqual(amount, (D('26000') * (w - 12) / w).quantize(D('0.01')))

    def test_no_run_month_flagged_verify(self):
        amount, lines = compute_correction(self.emp, datetime.date(2026, 8, 1))
        aug = [l for l in lines if l['month'] == 8][0]
        self.assertFalse(aug['had_run'])
        self.assertTrue(any('No payroll run for Aug 2026' in w and 'VERIFY' in w for w in aug['warnings']))
        self.assertEqual(amount, D('52000.00'))

    def test_recovery_when_doj_moves_later(self):
        amount, lines = compute_correction(self.emp, datetime.date(2026, 10, 16))
        self.assertLess(amount, 0)
        self.assertEqual(lines[0]['month'], 10)
        w = correction_warnings(amount, lines)
        self.assertIn(RECOVERY_PF_WARNING, w)
        self.assertIn(f'Recovery: {ESIC_FLAG}', w)

    def test_warnings_and_recalc_notice(self):
        amount, lines = compute_correction(self.emp, datetime.date(2026, 9, 1))
        w = correction_warnings(amount, lines, self.t, (2026, 11))
        self.assertIn(ARREARS_PF_WARNING, w)
        self.assertIn(f'Arrears: {ESIC_FLAG}', w)
        self.assertTrue(any('recalculate' in x for x in w))

    def test_apply_targets_november_and_recompute_zero_record(self):
        # A ₹0 record saved before the fix, DOJ already moved.
        old = DOJCorrection.objects.create(
            tenant=self.t, employee=self.emp, old_doj=datetime.date(2026, 10, 1),
            new_doj=datetime.date(2026, 9, 1), reason='UAT test', corrected_by=self.t, amount=0,
            breakdown=[], target_month=12, target_year=2026)
        Employee.objects.filter(pk=self.emp.pk).update(date_of_joining=datetime.date(2026, 9, 1))
        self.emp.refresh_from_db()
        corr = apply_doj_correction(self.emp, datetime.date(2026, 9, 1), 'fix', self.t, recompute=True)
        self.assertEqual(corr.pk, old.pk)
        self.assertEqual(corr.amount, D('26000.00'))
        self.assertEqual((corr.target_month, corr.target_year), (11, 2026))
        self.assertEqual(DOJCorrection.objects.count(), 1)


class R12RudraFollowUpTests(TestCase):
    """Rudra #224 follow-up: R12 numbers, per-month lines, history, edit window."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.t = User.objects.create_user(username='r12b', email='r12b@x.in', password='pw', role='admin')
        dept = Department.objects.create(tenant=self.t, name='Ops')
        des = Designation.objects.create(tenant=self.t, name='Exec')
        st = SalaryStructure.objects.create(tenant=self.t, name='S')
        SalaryComponent.objects.create(salary_structure=st, name='Basic', component_type='fixed',
                                       value='0', is_basic=True)
        self.emp = Employee.objects.create(
            tenant=self.t, employee_code='R12', full_name='R12 MidYear', date_of_birth='1990-01-01',
            date_of_joining=datetime.date(2026, 10, 1), gender='M', employment_type='full_time',
            designation=des, department=dept, status='active', work_state='Odisha')
        # ₹20,00,000 annual CTC → monthly_ctc 166666.67; Basic 50% + HRA, balance in Special Allowance.
        EmployeeSalaryAssignment.objects.create(
            tenant=self.t, employee=self.emp, salary_structure=st, effective_from='2026-10-01',
            monthly_ctc=D('166666.67'),
            computed_components={'Basic': '83333.34', 'HRA': '41666.67', 'Special Allowance': '41666.66'})
        PayrollRun.objects.create(tenant=self.t, month=9, year=2026, status='approved')
        octr = PayrollRun.objects.create(tenant=self.t, month=10, year=2026, status='approved')
        Payslip.objects.create(tenant=self.t, payroll_run=octr, employee=self.emp, total_working_days=27,
                               present_days=27, gross_salary=D('166666.67'), net_salary=D('150000'))
        self.nov = PayrollRun.objects.create(tenant=self.t, month=11, year=2026, status='calculated')
        self.api = APIClient()
        self.api.force_authenticate(self.t)

    def test_r12_line_is_september_only(self):
        amount, lines = compute_correction(self.emp, datetime.date(2026, 9, 1))
        self.assertEqual([(l['year'], l['month']) for l in lines], [(2026, 9)])  # October gives 0
        ln = lines[0]
        self.assertEqual(ln['label'], 'Sep 2026')
        self.assertEqual(ln['monthly_gross'], '166666.67')
        self.assertEqual((ln['old_payable_days'], ln['new_payable_days']), (0, ln['working_days']))
        self.assertEqual(amount, D('166666.67'))

    def test_payslip_lines_per_month(self):
        from hr.services.doj_correction import pending_for_payslip
        apply_doj_correction(self.emp, datetime.date(2026, 9, 1), 'Offer letter', self.t)
        res = pending_for_payslip(self.emp, 11, 2026)
        labels = [l for l, _a in res['earning_lines']]
        self.assertEqual(len(labels), 1)
        self.assertIn('for Sep 2026', labels[0])
        self.assertIn('gross ₹166666.67', labels[0])
        self.assertEqual(res['breakdown'][0]['label'], 'Sep 2026')
        self.assertEqual(res['breakdown'][0]['amount'], '166666.67')

    def test_month_without_run_uses_structure_in_force(self):
        EmployeeSalaryAssignment.objects.create(
            tenant=self.t, employee=self.emp, salary_structure=SalaryStructure.objects.get(tenant=self.t),
            effective_from='2026-08-01', monthly_ctc=D('100000.00'),
            computed_components={'Basic': '50000.00', 'HRA': '25000.00', 'Special Allowance': '25000.00'})
        amount, lines = compute_correction(self.emp, datetime.date(2026, 8, 1))
        aug = [l for l in lines if l['month'] == 8][0]
        sep = [l for l in lines if l['month'] == 9][0]
        self.assertEqual(aug['monthly_gross'], '100000.00')  # in force in August, not the October one
        self.assertTrue(any('VERIFY' in w for w in aug['warnings']))
        self.assertEqual(sep['monthly_gross'], '100000.00')

    def _zero_record(self):
        c = DOJCorrection.objects.create(
            tenant=self.t, employee=self.emp, old_doj=datetime.date(2026, 10, 1),
            new_doj=datetime.date(2026, 9, 1), reason='UAT test', corrected_by=self.t, amount=0,
            breakdown=[], target_month=12, target_year=2026)
        Employee.objects.filter(pk=self.emp.pk).update(date_of_joining=datetime.date(2026, 9, 1))
        self.emp.refresh_from_db()
        return c

    def test_recompute_keeps_history(self):
        c = self._zero_record()
        corr = apply_doj_correction(self.emp, datetime.date(2026, 9, 1), 'fix', self.t, recompute=True)
        self.assertEqual(corr.pk, c.pk)
        h = corr.history[-1]
        self.assertEqual(h['action'], 'recompute')
        self.assertEqual(h['old']['amount'], '0.00')
        self.assertEqual((h['old']['target_month'], h['old']['target_year']), (12, 2026))
        self.assertEqual(h['reason'], 'fix')
        self.assertEqual(h['by_email'], 'r12b@x.in')

    def test_recompute_blocked_once_target_run_approved(self):
        c = self._zero_record()
        c.target_month = 11
        c.save()
        self.nov.status = 'approved'
        self.nov.save()
        r = self.api.post(f'/api/hr/employees/{self.emp.pk}/doj-correction/',
                          {'new_doj': '2026-09-01', 'reason': 'fix', 'recompute': True}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Create a new DOJ correction', r.data['error'])

    def test_admin_edit_amount_and_window(self):
        corr = apply_doj_correction(self.emp, datetime.date(2026, 9, 1), 'Offer letter', self.t)
        url = f'/api/hr/employees/{self.emp.pk}/doj-corrections/{corr.pk}/adjust/'
        r = self.api.post(url, {'lines': [{'year': 2026, 'month': 9, 'amount': '160000'}]}, format='json')
        self.assertEqual(r.status_code, 400, getattr(r, "data", r.content[:300]))  # reason required
        r = self.api.post(url, {'lines': [{'year': 2026, 'month': 9, 'amount': '160000'}],
                                'reason': 'Agreed with employee'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        corr.refresh_from_db()
        self.assertEqual(corr.amount, D('160000.00'))
        self.assertEqual(corr.breakdown[0]['computed_delta'], '166666.67')
        self.assertEqual(corr.history[-1]['action'], 'edit')
        lst = self.api.get(f'/api/hr/employees/{self.emp.pk}/doj-corrections/')
        self.assertTrue(lst.data[0]['editable'])
        self.nov.status = 'approved'
        self.nov.save()
        r = self.api.post(url, {'lines': [{'year': 2026, 'month': 9, 'amount': '1'}], 'reason': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)

import datetime
from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import (Department, Designation, Employee, EmployeeSalaryAssignment, PayrollRun, Payslip,
                       SalaryComponent, SalaryStructure)
from hr.services.doj_correction import monthly_full_gross
from hr.services.payroll_engine import compute_gross, run_payroll
from users.models import User


class EngineTieBreakTests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='et', email='et@x.in', password='pw', role='admin')
        d = Department.objects.create(tenant=self.t, name='D')
        g = Designation.objects.create(tenant=self.t, name='G')
        self.st = SalaryStructure.objects.create(tenant=self.t, name='S')
        SalaryComponent.objects.create(salary_structure=self.st, name='Basic', component_type='fixed', value='0',
                                       is_basic=True)
        self.e = Employee.objects.create(tenant=self.t, employee_code='E1', full_name='Tie', date_of_birth='1990-01-01',
                                         date_of_joining=datetime.date(2026, 1, 1), gender='M',
                                         employment_type='full_time', designation=g, department=d,
                                         status='active', work_state='Karnataka',
                                         bank_account_number='1', bank_ifsc='SBIN0001234')

    def _two(self, newest_ctc='45000', older_ctc='30000'):
        a = EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=self.e, salary_structure=self.st,
                                                    effective_from='2026-04-01', monthly_ctc=D(older_ctc),
                                                    computed_components={'Basic': str(D(older_ctc) / 2)})
        b = EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=self.e, salary_structure=self.st,
                                                    effective_from='2026-04-01', monthly_ctc=D(newest_ctc),
                                                    computed_components={'Basic': str(D(newest_ctc) / 2)})
        EmployeeSalaryAssignment.objects.filter(pk=a.pk).update(created_at=b.created_at - datetime.timedelta(seconds=5))
        return a, b

    def test_engine_and_doj_use_most_recent_on_same_date(self):
        _a, b = self._two()
        _g, assignment, _f, _e, _d = compute_gross(self.e, 9, 2026)
        self.assertEqual(assignment.pk, b.pk)
        self.assertEqual(monthly_full_gross(self.e, 2026, 9), D('45000.00'))
        r = PayrollRun.objects.create(tenant=self.t, month=9, year=2026, status='draft')
        run_payroll(str(r.id))
        self.assertEqual(Payslip.objects.get(payroll_run=r, employee=self.e).gross_salary, D('45000.00'))

    def test_save_same_date_returns_warning(self):
        EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=self.e, salary_structure=self.st,
                                                effective_from='2026-04-01', monthly_ctc=D('30000'))
        api = APIClient()
        api.force_authenticate(self.t)
        resp = api.post('/api/hr/salary-assignments/', {'employee': str(self.e.id), 'salary_structure': str(self.st.id),
                                                       'effective_from': '2026-04-01', 'monthly_ctc': '45000'},
                        format='json')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertIn('This replaces the assignment effective 01-04-2026', resp.data['warnings'][0])
        resp = api.post('/api/hr/salary-assignments/', {'employee': str(self.e.id), 'salary_structure': str(self.st.id),
                                                       'effective_from': '2026-05-01', 'monthly_ctc': '46000'},
                        format='json')
        self.assertNotIn('warnings', resp.data)

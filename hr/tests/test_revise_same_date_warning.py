import datetime
from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import Department, Designation, Employee, EmployeeSalaryAssignment, SalaryStructure
from users.models import User


class ReviseSameDateWarningTests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='rv', email='rv@x.in', password='pw', role='admin')
        d = Department.objects.create(tenant=self.t, name='D')
        g = Designation.objects.create(tenant=self.t, name='G')
        self.st = SalaryStructure.objects.create(tenant=self.t, name='S')
        self.e = Employee.objects.create(tenant=self.t, employee_code='Q1', full_name='QA PT Default',
                                         date_of_birth='1990-01-01', date_of_joining=datetime.date(2026, 1, 1),
                                         gender='M', employment_type='full_time', designation=g, department=d,
                                         status='active', work_state='Odisha')
        self.api = APIClient()
        self.api.force_authenticate(self.t)
        self.url = f'/api/hr/employees/{self.e.id}/increment_salary/'

    def _assign(self, ctc, created_offset):
        a = EmployeeSalaryAssignment.objects.create(tenant=self.t, employee=self.e, salary_structure=self.st,
                                                    effective_from='2026-11-01', monthly_ctc=D(ctc))
        EmployeeSalaryAssignment.objects.filter(pk=a.pk).update(
            created_at=a.created_at + datetime.timedelta(seconds=created_offset))
        return a

    def test_warning_and_latest_same_date_assignment_updated(self):
        self._assign('30000', -20)
        newest = self._assign('31000', 0)
        r = self.api.post(self.url, {'new_salary': '32000', 'effective_from': '2026-11-01', 'reason': 'Revision'},
                          format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('This replaces the assignment effective 01-11-2026', r.data['warnings'][0])
        newest.refresh_from_db()
        self.assertEqual(newest.monthly_ctc, D('32000.00'))  # the one payroll and the list use

    def test_no_warning_for_new_date(self):
        self._assign('30000', 0)
        r = self.api.post(self.url, {'new_salary': '32000', 'effective_from': '2026-12-01', 'reason': 'Revision'},
                          format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn('warnings', r.data)

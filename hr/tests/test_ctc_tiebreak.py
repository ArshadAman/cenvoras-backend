import datetime
from decimal import Decimal as D

from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import Department, Designation, Employee, EmployeeSalaryAssignment, SalaryStructure
from users.models import User


class CtcTieBreakTests(TestCase):
    def test_same_effective_date_latest_created_wins(self):
        t = User.objects.create_user(username='tb', email='tb@x.in', password='pw', role='admin')
        d = Department.objects.create(tenant=t, name='D')
        g = Designation.objects.create(tenant=t, name='G')
        st = SalaryStructure.objects.create(tenant=t, name='S')
        e = Employee.objects.create(tenant=t, employee_code='T1', full_name='Tie', date_of_birth='1990-01-01',
                                    date_of_joining=datetime.date(2026, 1, 1), gender='M',
                                    employment_type='full_time', designation=g, department=d, status='active',
                                    work_state='Odisha')
        old = EmployeeSalaryAssignment.objects.create(tenant=t, employee=e, salary_structure=st,
                                                      effective_from='2026-04-01', monthly_ctc=D('30000'))
        new = EmployeeSalaryAssignment.objects.create(tenant=t, employee=e, salary_structure=st,
                                                      effective_from='2026-04-01', monthly_ctc=D('45000'))
        # Make created_at strictly ordered regardless of clock resolution.
        EmployeeSalaryAssignment.objects.filter(pk=old.pk).update(
            created_at=new.created_at - datetime.timedelta(seconds=5))
        api = APIClient()
        api.force_authenticate(t)
        r = api.get('/api/hr/employees/')
        rows = r.data.get('results', r.data) if isinstance(r.data, dict) else r.data
        row = [x for x in rows if x['employee_code'] == 'T1'][0]
        self.assertEqual(D(row['current_ctc']), D('45000'))
        # Flip creation order: the other one must win.
        EmployeeSalaryAssignment.objects.filter(pk=old.pk).update(
            created_at=new.created_at + datetime.timedelta(seconds=5))
        r = api.get('/api/hr/employees/')
        rows = r.data.get('results', r.data) if isinstance(r.data, dict) else r.data
        row = [x for x in rows if x['employee_code'] == 'T1'][0]
        self.assertEqual(D(row['current_ctc']), D('30000'))

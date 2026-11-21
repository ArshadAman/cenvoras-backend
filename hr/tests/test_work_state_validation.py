import datetime
from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from hr.models import Department, Designation, Employee
from hr.serializers import EmployeeSerializer
from hr.services.work_state import canonical_work_state
from users.models import User


class WorkStateValidationTests(TestCase):
    def test_canonical(self):
        self.assertEqual(canonical_work_state('odisha'), 'Odisha')
        self.assertEqual(canonical_work_state('  TAMIL  nadu '), 'Tamil Nadu')
        self.assertEqual(canonical_work_state('Orissa'), 'Odisha')
        self.assertEqual(canonical_work_state('21'), 'Odisha')
        self.assertIsNone(canonical_work_state('c'))
        self.assertIsNone(canonical_work_state('Narnia'))

    def test_serializer(self):
        s = EmployeeSerializer()
        self.assertEqual(s.validate_work_state('karnataka'), 'Karnataka')
        self.assertEqual(s.validate_work_state(''), '')
        with self.assertRaises(ValidationError):
            s.validate_work_state('c')

    def test_report_flags_invalid(self):
        t = User.objects.create_user(username='ws', email='ws@x.in', password='pw', role='admin')
        t.state = 'Odisha'
        t.save()
        d = Department.objects.create(tenant=t, name='D')
        g = Designation.objects.create(tenant=t, name='G')
        Employee.objects.create(tenant=t, employee_code='E9', full_name='Bad', date_of_birth='1990-01-01',
                                date_of_joining=datetime.date(2026, 8, 1), gender='M',
                                employment_type='full_time', designation=g, department=d,
                                status='active', work_state='c')
        out = StringIO()
        call_command('report_work_state_mismatch', stdout=out)
        self.assertIn('INVALID', out.getvalue())
        self.assertEqual(Employee.objects.get(employee_code='E9').work_state, 'c')

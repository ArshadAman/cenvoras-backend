"""UAT P1 #10: DOJ correction — block plain edit, arrears/recovery maths, next open payroll."""
import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.exceptions import ValidationError

import hr.services.payroll_engine as engine
from hr.models import (
    Department, Designation, DOJCorrection, Employee, EmployeeSalaryAssignment, PayrollRun, Payslip,
    SalaryComponent, SalaryStructure,
)
from hr.serializers import EmployeeSerializer
from hr.services.doj_correction import (
    DOJCorrectionError, apply_doj_correction, month_delta, prejoin_days,
)
from users.models import User

D = Decimal
SUN = {7}


class DOJMathTests(TestCase):
    def test_prejoin_days_sunday_only(self):
        # Aug 2026: 1st is a Saturday; 1–15 Aug has Sundays 2 and 9 → 13 working days before 16 Aug.
        self.assertEqual(prejoin_days(datetime.date(2026, 8, 16), 2026, 8, 26, SUN), 13)
        self.assertEqual(prejoin_days(datetime.date(2026, 8, 1), 2026, 8, 26, SUN), 0)
        self.assertEqual(prejoin_days(datetime.date(2026, 9, 1), 2026, 8, 26, SUN), 26)

    def test_later_doj_is_recovery(self):
        delta, old_p, new_p = month_delta(D('26000'), 26, datetime.date(2026, 8, 1),
                                          datetime.date(2026, 8, 16), 2026, 8, SUN)
        self.assertEqual((old_p, new_p), (26, 13))
        self.assertEqual(delta, D('-13000.00'))

    def test_earlier_doj_is_arrears(self):
        # Excluded from the July run; DOJ moves to Mon 20 Jul → 16 pre-join days, 10 payable of 26.
        delta, old_p, new_p = month_delta(D('26000'), 26, datetime.date(2026, 8, 1),
                                          datetime.date(2026, 7, 20), 2026, 7, SUN)
        self.assertEqual((old_p, new_p), (0, 10))
        self.assertEqual(delta, D('10000.00'))

    def test_rounding_is_half_up_decimal(self):
        delta, _, _ = month_delta(D('10000'), 26, datetime.date(2026, 8, 1),
                                  datetime.date(2026, 8, 4), 2026, 8, SUN)  # 2 days: 10000*24/26 - 10000
        self.assertEqual(delta, D('-769.23'))


class DOJCorrectionFlowTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(username='doj_t', email='doj_t@x.in', password='pw', role='admin')
        dept = Department.objects.create(tenant=self.tenant, name='Ops')
        desig = Designation.objects.create(tenant=self.tenant, name='Exec')
        struct = SalaryStructure.objects.create(tenant=self.tenant, name='S')
        SalaryComponent.objects.create(salary_structure=struct, name='Basic', component_type='fixed',
                                       value='13000.00', is_basic=True)
        SalaryComponent.objects.create(salary_structure=struct, name='HRA', component_type='fixed',
                                       value='13000.00', is_basic=False)
        self.emp = Employee.objects.create(
            tenant=self.tenant, employee_code='E1', full_name='E One', date_of_birth='1990-01-01',
            date_of_joining=datetime.date(2026, 8, 1), gender='M', employment_type='full_time',
            designation=desig, department=dept, status='active', work_state='Odisha')
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=self.emp, salary_structure=struct, effective_from='2026-07-01',
            monthly_ctc=D('26000.00'), computed_components={'Basic': '13000.00', 'HRA': '13000.00'})
        for m in (8, 9):
            run = PayrollRun.objects.create(tenant=self.tenant, month=m, year=2026, status='approved')
            Payslip.objects.create(tenant=self.tenant, payroll_run=run, employee=self.emp,
                                   total_working_days=26, present_days=26, gross_salary=D('26000'), net_salary=D('26000'))

    def test_plain_edit_blocked_once_payroll_exists(self):
        s = EmployeeSerializer(self.emp, data={'date_of_joining': '2026-08-16'}, partial=True,
                               context={'request': SimpleNamespace(user=self.tenant)})
        with self.assertRaises(ValidationError):
            s.validate_date_of_joining(datetime.date(2026, 8, 16))

    def test_plain_edit_allowed_without_payroll(self):
        Payslip.objects.all().delete()
        s = EmployeeSerializer(self.emp, context={'request': SimpleNamespace(user=self.tenant)})
        self.assertEqual(s.validate_date_of_joining(datetime.date(2026, 8, 16)), datetime.date(2026, 8, 16))

    def test_admin_only_and_reason_required(self):
        hr_user = SimpleNamespace(role='hr', email='')
        with self.assertRaises(DOJCorrectionError):
            apply_doj_correction(self.emp, datetime.date(2026, 8, 16), 'typo', hr_user)
        with self.assertRaises(DOJCorrectionError):
            apply_doj_correction(self.emp, datetime.date(2026, 8, 16), '  ', self.tenant)

    def test_later_doj_recovery_in_next_open_month_locked_months_untouched(self):
        before = list(Payslip.objects.values_list('gross_salary', 'net_salary'))
        c = apply_doj_correction(self.emp, datetime.date(2026, 8, 16), 'Offer letter says 16 Aug', self.tenant)
        self.assertEqual(c.amount, D('-13000.00'))
        self.assertEqual(c.kind, 'recovery')
        self.assertEqual((c.target_month, c.target_year), (10, 2026))
        self.assertEqual([l['month'] for l in c.breakdown], [8])
        self.assertEqual(list(Payslip.objects.values_list('gross_salary', 'net_salary')), before)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.date_of_joining, datetime.date(2026, 8, 16))

    def test_earlier_doj_arrears_for_excluded_run_month(self):
        PayrollRun.objects.create(tenant=self.tenant, month=7, year=2026, status='locked')
        with patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
            c = apply_doj_correction(self.emp, datetime.date(2026, 7, 20), 'Joined earlier', self.tenant)
        self.assertEqual(c.amount, D('10000.00'))
        self.assertEqual(c.kind, 'arrears')

    def test_line_lands_in_next_open_payroll_gross(self):
        apply_doj_correction(self.emp, datetime.date(2026, 8, 16), 'Offer letter says 16 Aug', self.tenant)
        run = PayrollRun.objects.create(tenant=self.tenant, month=10, year=2026, status='draft')
        bd = {'present_days': D('26'), 'payable_days': D('26'), 'paid_leave_days': D('0'),
              'absent_days': D('0'), 'lop_days': D('0'), 'days_before_joining': 0,
              'absent_dates': [], 'unpaid_leave_dates': []}
        with patch('hr.services.payroll_engine.get_attendance_breakdown', return_value=bd), \
             patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
            ps = engine.compute_payslip_for_employee(self.emp, run)
        self.assertEqual(ps.gross_salary, D('13000.00'))
        self.assertTrue(any(k.startswith('DOJ correction recovery') for k in ps.earnings))
        self.assertEqual(DOJCorrection.objects.count(), 1)

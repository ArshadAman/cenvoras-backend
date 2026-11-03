"""Rudra CA: monthly gross == monthly CTC, never above it; same rule in engine and DOJ correction."""
import datetime
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase

from hr.models import (Department, Designation, Employee, EmployeeSalaryAssignment, SalaryComponent,
                       SalaryStructure)
from hr.services.doj_correction import monthly_full_gross
from hr.services.payroll_engine import balance_earnings_to_ctc, compute_gross
from users.models import User


class BalanceTests(SimpleTestCase):
    def test_below_ctc_tops_up_special(self):
        e, g, n = balance_earnings_to_ctc({'Basic': D('10000'), 'HRA': D('5000')}, D('30000'))
        self.assertEqual(g, D('30000'))
        self.assertEqual(e['Special Allowance'], D('15000'))
        self.assertEqual(n, [])

    def test_below_ctc_with_existing_special(self):
        e, g, _ = balance_earnings_to_ctc({'Basic': D('10000'), 'Special Allowance': D('1000')}, D('30000'))
        self.assertEqual((g, e['Special Allowance']), (D('30000'), D('20000')))

    def test_above_ctc_reduces_special_floor_zero(self):
        e, g, n = balance_earnings_to_ctc({'Basic': D('15000'), 'HRA': D('7500'), 'Special Allowance': D('10000')},
                                          D('30000'))
        self.assertEqual((g, e['Special Allowance'], n), (D('30000'), D('7500'), []))

    def test_above_ctc_without_room_flags_no_silent_scaling(self):
        e, g, n = balance_earnings_to_ctc({'Basic': D('20000'), 'HRA': D('20000'), 'Special Allowance': D('5000')},
                                          D('30000'))
        self.assertEqual(g, D('40000'))  # stored components, unscaled
        self.assertEqual((e['Basic'], e['Special Allowance']), (D('20000'), D('0')))
        self.assertTrue(n and n[0].startswith('VERIFY salary structure'))

    def test_above_ctc_scaled_when_confirmed(self):
        e, g, n = balance_earnings_to_ctc({'Basic': D('20000'), 'HRA': D('20000')}, D('30000'), allow_scaling=True)
        self.assertEqual((g, e['Basic'], e['HRA']), (D('30000'), D('15000.00'), D('15000.00')))
        self.assertFalse(any(x.startswith('VERIFY') for x in n))

    def test_zero_ctc_unchanged(self):
        _e, g, _ = balance_earnings_to_ctc({'Basic': D('100')}, D('0'))
        self.assertEqual(g, D('100'))


class EngineAndDojAgreeTests(TestCase):
    def test_engine_and_doj_cap_at_ctc(self):
        t = User.objects.create_user(username='gc', email='gc@x.in', password='pw', role='admin')
        d = Department.objects.create(tenant=t, name='D')
        g = Designation.objects.create(tenant=t, name='G')
        st = SalaryStructure.objects.create(tenant=t, name='S')
        SalaryComponent.objects.create(salary_structure=st, name='Basic', component_type='fixed', value='0',
                                       is_basic=True)
        emp = Employee.objects.create(tenant=t, employee_code='G1', full_name='G', date_of_birth='1990-01-01',
                                      date_of_joining=datetime.date(2026, 1, 1), gender='M',
                                      employment_type='full_time', designation=g, department=d,
                                      status='active', work_state='Odisha')
        EmployeeSalaryAssignment.objects.create(
            tenant=t, employee=emp, salary_structure=st, effective_from='2026-01-01', monthly_ctc=D('30000'),
            computed_components={'Basic': '15000', 'HRA': '7500', 'Special Allowance': '12000'})
        gross, _a, factor, earnings, _d = compute_gross(emp, 9, 2026)
        self.assertEqual(factor, 1)
        self.assertEqual(gross, D('30000.00'))
        self.assertEqual(earnings['Special Allowance'], D('7500.00'))
        self.assertEqual(monthly_full_gross(emp, 2026, 9), D('30000.00'))

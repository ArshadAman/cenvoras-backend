"""Karnataka PT FY 2026-27 (Rudra CA sign-off, 22 Oct 2026).

₹200/mo (Apr–Jan, Mar), ₹300 in February, when ACTUAL monthly gross (after LOP)
>= ₹25,000; nil below. Exempt: 40%+ disability, armed forces. Versioned from 1 Apr 2025.
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

import hr.services.payroll_engine as engine
from hr.models import (
    Department, Designation, Employee, EmployeeSalaryAssignment, PayrollRun,
    ProfessionalTaxSlab, SalaryComponent, SalaryStructure,
)
from hr.services.payroll_engine import compute_pt
from users.models import User


class KarnatakaPTSlabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs')

    def test_seed_is_versioned_from_1_apr_2025_with_feb_override(self):
        top = ProfessionalTaxSlab.objects.get(state_name='Karnataka', lower_bound=Decimal('25000'))
        self.assertEqual(top.effective_from, datetime.date(2025, 4, 1))
        self.assertEqual(top.pt_amount, Decimal('200.00'))
        self.assertEqual(top.february_amount, Decimal('300.00'))

    def test_every_month_of_fy2627(self):
        for month, year in [(m, 2026) for m in range(4, 13)] + [(m, 2027) for m in (1, 2, 3)]:
            expected = Decimal('300.00') if month == 2 else Decimal('200.00')
            with self.subTest(month=month):
                self.assertEqual(
                    compute_pt(Decimal('25000.00'), 'Karnataka', month=month,
                               on_date=datetime.date(year, month, 28)),
                    expected,
                )

    def test_below_threshold_nil_including_february(self):
        for month in (2, 10):
            self.assertEqual(
                compute_pt(Decimal('24999.99'), 'KA', month=month,
                           on_date=datetime.date(2027 if month == 2 else 2026, month, 28)),
                Decimal('0.00'),
            )

    def test_february_amount_is_configurable_per_state(self):
        ProfessionalTaxSlab.objects.create(
            state_name='Testland', lower_bound=Decimal('0'), upper_bound=None,
            pt_amount=Decimal('100'), february_amount=Decimal('150'),
        )
        self.assertEqual(compute_pt(Decimal('5000'), 'Testland', month=2), Decimal('150.00'))
        self.assertEqual(compute_pt(Decimal('5000'), 'Testland', month=3), Decimal('100.00'))
        # States without an override (e.g. Maharashtra here) are unchanged in Feb
        self.assertEqual(compute_pt(Decimal('50000'), 'Maharashtra', month=2), Decimal('200.00'))


class KarnatakaPTPayslipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs')
        cls.tenant = User.objects.create_user(username='ka_pt', email='ka_pt@example.com', password='pw')
        dept = Department.objects.create(tenant=cls.tenant, name='Ops')
        desig = Designation.objects.create(tenant=cls.tenant, name='Exec')
        struct = SalaryStructure.objects.create(tenant=cls.tenant, name='KA')
        SalaryComponent.objects.create(salary_structure=struct, name='Basic', component_type='fixed', value='13000.00', is_basic=True)
        SalaryComponent.objects.create(salary_structure=struct, name='HRA', component_type='fixed', value='13000.00', is_basic=False)
        cls.struct = struct
        cls.dept, cls.desig = dept, desig

    def _employee(self, code, **flags):
        emp = Employee.objects.create(
            tenant=self.tenant, employee_code=code, full_name=code,
            date_of_birth='1990-01-01', date_of_joining='2020-01-01', gender='M',
            employment_type='full_time', designation=self.desig, department=self.dept,
            status='active', work_state='Karnataka', **flags,
        )
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=emp, salary_structure=self.struct,
            effective_from='2025-01-01', monthly_ctc=Decimal('26000.00'),
            computed_components={'Basic': '13000.00', 'HRA': '13000.00'},
        )
        return emp

    def _payslip(self, emp, month, year, present):
        run, _ = PayrollRun.objects.get_or_create(tenant=self.tenant, month=month, year=year, defaults={'status': 'draft'})
        present = Decimal(present)
        lop = Decimal('26') - present
        breakdown = {
            'present_days': present, 'payable_days': present, 'paid_leave_days': Decimal('0'),
            'absent_days': lop, 'lop_days': lop, 'days_before_joining': 0,
            'absent_dates': [], 'unpaid_leave_dates': [],
        }
        with patch('hr.services.payroll_engine.get_attendance_breakdown', return_value=breakdown), \
             patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
            return engine.compute_payslip_for_employee(emp, run)

    def test_february_full_month_is_300(self):
        ps = self._payslip(self._employee('KA-FEB'), 2, 2027, '26')
        self.assertEqual(ps.gross_salary, Decimal('26000.00'))
        self.assertEqual(ps.professional_tax, Decimal('300.00'))

    def test_regular_month_full_is_200(self):
        ps = self._payslip(self._employee('KA-OCT'), 10, 2026, '26')
        self.assertEqual(ps.professional_tax, Decimal('200.00'))

    def test_lop_drops_actual_gross_below_25k_is_nil(self):
        # 2 LOP days: 26,000 x 24/26 = 24,000 actual gross -> nil PT, even in February
        ps = self._payslip(self._employee('KA-LOP'), 2, 2027, '24')
        self.assertLess(ps.gross_salary, Decimal('25000.00'))
        self.assertEqual(ps.professional_tax, Decimal('0.00'))

    def test_disability_and_armed_forces_exempt(self):
        for code, flags in (('KA-DIS', {'pt_exempt_disability': True}),
                            ('KA-ARM', {'pt_exempt_armed_forces': True})):
            with self.subTest(code=code):
                ps = self._payslip(self._employee(code, **flags), 2, 2027, '26')
                self.assertEqual(ps.professional_tax, Decimal('0.00'))

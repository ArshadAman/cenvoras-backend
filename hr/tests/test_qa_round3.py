"""QA Round 3: PT persist, PF fallback, 87A honesty, profile next_steps, ledger stats."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User
from hr.models import (
    Department, Designation, Employee,
    SalaryStructure, EmployeeSalaryAssignment, PayrollRun,
)
from hr.services.payroll_engine import compute_payslip_for_employee, run_payroll
from hr.services.work_state import resolve_work_state
from hr.serializers import EmployeeSerializer
from hr.services.audit_service import _normalize_update_changes
from audit_log.serializers import normalize_audit_changes


class PTPersistAndPFFallbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs')

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='r3_pt', email='r3pt@ex.com', password='x',
            business_name='R3 Co', role='admin', state='Karnataka',
        )
        self.dept = Department.objects.create(tenant=self.tenant, name='Ops')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Staff')
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)

    def test_blank_work_state_persisted_on_retrieve(self):
        emp = Employee.objects.create(
            tenant=self.tenant, full_name='Blank State',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=self.dept, designation=self.desig,
            work_state='', status='active',
            personal_phone='9876543210', bank_name='SBI',
            bank_account_number='1234567890', bank_ifsc='SBIN0001234',
            account_holder_name='Blank State',
        )
        structure = SalaryStructure.objects.create(tenant=self.tenant, name='Std')
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=emp, salary_structure=structure,
            monthly_ctc=Decimal('50000.00'), effective_from=datetime.date(2024, 1, 1),
            computed_components={'Basic': '25000.00', 'HRA': '12500.00', 'Special Allowance': '12500.00'},
        )
        res = self.client.get(f'/api/hr/employees/{emp.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        emp.refresh_from_db()
        self.assertEqual(emp.work_state, 'Karnataka')
        sd = res.data.get('salary_details') or {}
        self.assertGreater(Decimal(sd['employee_deductions']['professional_tax']), Decimal('0'))

    def test_pf_fallback_without_is_basic(self):
        emp = Employee.objects.create(
            tenant=self.tenant, full_name='No Basic Flag',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=self.dept, designation=self.desig,
            work_state='Karnataka', status='active',
            personal_phone='9876543211', bank_name='SBI',
            bank_account_number='1234567891', bank_ifsc='SBIN0001234',
            account_holder_name='No Basic',
        )
        structure = SalaryStructure.objects.create(tenant=self.tenant, name='NoBasic')
        # No SalaryComponent with is_basic — computed_components still has Basic
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=emp, salary_structure=structure,
            monthly_ctc=Decimal('65000.00'), effective_from=datetime.date(2024, 1, 1),
            computed_components={'Basic': '32500.00', 'HRA': '16250.00', 'Special Allowance': '16250.00'},
        )
        run = PayrollRun.objects.create(
            tenant=self.tenant, month=10, year=2026, status='draft',
        )
        payslip = compute_payslip_for_employee(emp, run)
        self.assertIsNotNone(payslip)
        self.assertGreater(payslip.employee_pf, Decimal('0.00'))

    def test_high_ctc_no_false_87a_nil(self):
        emp = Employee.objects.create(
            tenant=self.tenant, full_name='Raihan Style',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=self.dept, designation=self.desig,
            work_state='Karnataka', status='active', tax_regime='new',
            personal_phone='9876543212', bank_name='SBI',
            bank_account_number='1234567892', bank_ifsc='SBIN0001234',
            account_holder_name='Raihan Style',
        )
        structure = SalaryStructure.objects.create(tenant=self.tenant, name='High')
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=emp, salary_structure=structure,
            monthly_ctc=Decimal('150000.00'), effective_from=datetime.date(2024, 1, 1),
            computed_components={'Basic': '75000.00', 'HRA': '37500.00', 'Special Allowance': '37500.00'},
        )
        res = self.client.get(f'/api/hr/employees/{emp.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        sd = res.data['salary_details']
        self.assertGreater(Decimal(sd['tds_details']['monthly_tds']), Decimal('0'))
        self.assertFalse(sd['tds_details']['nil_tax_87a'])


class ProfileNextStepsTests(TestCase):
    def test_placeholder_address_not_complete_message(self):
        u = User.objects.create_user(
            username='r3_prof', email='r3p@ex.com', password='x',
            business_name='Biz', role='admin',
            business_address='Main Address', first_name='A', last_name='B',
        )
        client = APIClient()
        client.force_authenticate(user=u)
        res = client.get('/api/users/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        steps = res.data['setup_progress']['next_steps']
        joined = ' '.join(steps).lower()
        self.assertNotIn('profile complete!', joined)
        self.assertIn('address', joined)
        self.assertLess(res.data['setup_progress']['completion_percentage'], 100)


class AuditNormalizeTests(TestCase):
    def test_before_after_to_field_deltas(self):
        changes = _normalize_update_changes(
            {'status': 'draft'}, {'status': 'calculated'}
        )
        self.assertEqual(changes['status']['old'], 'draft')
        self.assertEqual(changes['status']['new'], 'calculated')

    def test_serializer_legacy_blob(self):
        out = normalize_audit_changes('UPDATE', {
            'before': {'status': 'draft'},
            'after': {'status': 'approved'},
        })
        self.assertEqual(out['status']['new'], 'approved')


class LedgerStatsSmokeTests(TestCase):
    def test_ledger_stats_200(self):
        u = User.objects.create_user(
            username='r3_led', email='r3l@ex.com', password='x',
            business_name='Led', role='admin',
        )
        client = APIClient()
        client.force_authenticate(user=u)
        res = client.get('/api/ledger/accounts/stats/')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertIn('total_invoices', res.data)

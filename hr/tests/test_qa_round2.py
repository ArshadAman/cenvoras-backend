"""QA Round 2 root-cause tests: PT fallback, LOP display, payroll overdraft, attendance, docs, address."""
import datetime
import io
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User
from users.address_utils import is_meaningful_address
from hr.models import (
    Department, Designation, Employee, AttendanceRecord,
    SalaryStructure, EmployeeSalaryAssignment,
    PayrollRun, Payslip, HRDocument,
)
from hr.services.payroll_engine import compute_pt
from hr.services.work_state import resolve_work_state
from hr.serializers import PayslipSerializer


class AddressUtilsTests(TestCase):
    def test_placeholders_incomplete(self):
        self.assertFalse(is_meaningful_address(''))
        self.assertFalse(is_meaningful_address('   '))
        self.assertFalse(is_meaningful_address('Main Address'))
        self.assertFalse(is_meaningful_address('123 Main St'))
        self.assertFalse(is_meaningful_address('E.g. 12 MG Road'))
        self.assertTrue(is_meaningful_address('12 MG Road, Bengaluru 560001'))


class WorkStateResolveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs')
        cls.tenant = User.objects.create_user(
            username='pt_tenant', email='pt@ex.com', password='x',
            business_name='PT Co', role='admin', state='Karnataka',
        )
        cls.dept = Department.objects.create(tenant=cls.tenant, name='Ops')
        cls.desig = Designation.objects.create(tenant=cls.tenant, name='Staff')

    def test_empty_work_state_falls_back_to_company(self):
        emp = Employee.objects.create(
            tenant=self.tenant, full_name='No State',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=self.dept, designation=self.desig,
            work_state='', status='active',
        )
        self.assertEqual(resolve_work_state(employee=emp, tenant=self.tenant), 'Karnataka')
        self.assertEqual(
            compute_pt(Decimal('50000'), '', employee=emp, tenant=self.tenant),
            Decimal('200.00'),
        )

    def test_pt_amounts_for_qa_bands(self):
        for gross in ('50000', '65000', '100000', '147000'):
            with self.subTest(gross=gross):
                self.assertEqual(
                    compute_pt(Decimal(gross), 'Karnataka'),
                    Decimal('200.00'),
                )


class LOPAmountTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='lop_t', email='lop@ex.com', password='x',
            business_name='LOP Co', role='admin', state='Karnataka',
        )
        dept = Department.objects.create(tenant=self.tenant, name='Ops')
        desig = Designation.objects.create(tenant=self.tenant, name='Staff')
        self.emp = Employee.objects.create(
            tenant=self.tenant, full_name='LOP Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=dept, designation=desig,
            work_state='Karnataka', status='active',
        )
        structure = SalaryStructure.objects.create(tenant=self.tenant, name='Std')
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant, employee=self.emp, salary_structure=structure,
            monthly_ctc=Decimal('15000.00'), effective_from=datetime.date(2024, 1, 1),
            computed_components={'Basic': '7500.00', 'HRA': '3750.00', 'Special Allowance': '3750.00'},
        )
        self.run = PayrollRun.objects.create(
            tenant=self.tenant, month=10, year=2026, status='draft',
        )

    def test_lop_15000_x_3_of_27(self):
        ps = Payslip.objects.create(
            tenant=self.tenant, payroll_run=self.run, employee=self.emp,
            present_days=Decimal('24'), total_working_days=27,
            lop_days=Decimal('3'), gross_salary=Decimal('13333.33'),
            earnings={}, deductions={},
            deduction_reasons={
                'Loss of Pay (LOP)': {
                    'amount': '1666.67',
                    'reason': '3 day(s) LOP',
                }
            },
            net_salary=Decimal('12000.00'),
        )
        data = PayslipSerializer(ps).data
        self.assertEqual(Decimal(data['lop_amount']), Decimal('1666.67'))


class PayrollPayOverdraftTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='pay_od', email='payod@ex.com', password='x',
            business_name='Pay OD', role='admin', state='Karnataka',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        dept = Department.objects.create(tenant=self.tenant, name='Ops')
        desig = Designation.objects.create(tenant=self.tenant, name='Staff')
        self.emp = Employee.objects.create(
            tenant=self.tenant, full_name='Paid Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=dept, designation=desig,
            work_state='Karnataka', status='active',
        )
        self.run = PayrollRun.objects.create(
            tenant=self.tenant, month=9, year=2026, status='approved',
            total_net=Decimal('100.00'),  # stale / low — payslip is higher
        )
        Payslip.objects.create(
            tenant=self.tenant, payroll_run=self.run, employee=self.emp,
            present_days=Decimal('26'), total_working_days=26,
            lop_days=Decimal('0'), gross_salary=Decimal('50000'),
            earnings={}, deductions={}, net_salary=Decimal('45000.00'),
        )

    def test_pay_rejects_when_balance_below_payslip_sum(self):
        from ledger.services import AccountingService
        from ledger.models import GeneralLedgerEntry
        bank = AccountingService.get_or_create_default_accounts(self.tenant).get('1002')
        # Seed tiny positive balance
        GeneralLedgerEntry.objects.create(
            date=datetime.date.today(), account=bank,
            debit=Decimal('500.00'), credit=0,
            description='seed', reference='SEED-TINY', created_by=self.tenant,
        )
        res = self.client.post(
            f'/api/hr/payroll-runs/{self.run.id}/pay/',
            {'payment_account_id': str(bank.id)},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class AttendanceSummaryTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='att_t', email='att@ex.com', password='x',
            business_name='Att Co', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        dept = Department.objects.create(tenant=self.tenant, name='Ops')
        desig = Designation.objects.create(tenant=self.tenant, name='Staff')
        self.emp = Employee.objects.create(
            tenant=self.tenant, full_name='QA Mid',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=dept, designation=desig,
            work_state='Karnataka', status='active',
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=self.emp,
            date=datetime.date(2026, 10, 1), status='leave',
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=self.emp,
            date=datetime.date(2026, 10, 2), status='leave',
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=self.emp,
            date=datetime.date(2026, 10, 3), status='present',
        )

    def test_summary_counts_leave_and_present(self):
        res = self.client.get(
            '/api/hr/attendance/employee_summary/',
            {'employee_id': str(self.emp.id), 'month': 10, 'year': 2026},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        self.assertEqual(res.data['leave_days'], 2)
        self.assertEqual(res.data['present_days'], 1)
        self.assertTrue(res.data.get('days'))


class HRDocumentUploadTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='doc_t', email='doc@ex.com', password='x',
            business_name='Doc Co', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        self.dept = Department.objects.create(tenant=self.tenant, name='Ops')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Staff')
        self.emp = Employee.objects.create(
            tenant=self.tenant, full_name='Doc Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=self.dept, designation=self.desig,
            work_state='Karnataka', status='active',
        )

    def test_small_png_upload_201(self):
        # Minimal valid-ish PNG header bytes
        png = SimpleUploadedFile(
            'tiny.png',
            b'\x89PNG\r\n\x1a\n' + b'\x00' * 60,
            content_type='image/png',
        )
        res = self.client.post(
            '/api/hr/documents/',
            {
                'employee': str(self.emp.id),
                'title': 'ID Proof',
                'document_type': 'id_proof',
                'file': png,
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        doc = HRDocument.objects.get(employee=self.emp)
        self.assertIn(str(self.tenant.id), doc.file.name)
        self.assertIn(str(self.emp.id), doc.file.name)
        self.assertIn('id_proof', doc.file.name)
        self.assertTrue(res.data.get('file_url') or res.data.get('file'))

    def test_bad_employee_400(self):
        other = User.objects.create_user(
            username='other_t', email='o@ex.com', password='x',
            business_name='Other', role='admin',
        )
        odept = Department.objects.create(tenant=other, name='X')
        odesig = Designation.objects.create(tenant=other, name='Y')
        alien = Employee.objects.create(
            tenant=other, full_name='Alien',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M', employment_type='full_time',
            department=odept, designation=odesig,
            work_state='Karnataka', status='active',
        )
        png = SimpleUploadedFile('x.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 20, content_type='image/png')
        res = self.client.post(
            '/api/hr/documents/',
            {
                'employee': str(alien.id),
                'title': 'Bad',
                'document_type': 'other',
                'file': png,
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class ProfileCompletionPlaceholderTests(TestCase):
    def test_main_address_not_complete(self):
        u = User.objects.create_user(
            username='prof', email='p@ex.com', password='x',
            business_name='Biz', role='admin',
            business_address='Main Address',
        )
        u.mark_profile_completed()
        u.refresh_from_db()
        self.assertFalse(u.profile_completed)
        client = APIClient()
        client.force_authenticate(user=u)
        res = client.get('/api/users/profile/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        pct = res.data['setup_progress']['completion_percentage']
        self.assertLess(pct, 100)

import datetime
from decimal import Decimal
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from hr.models import (
    Department, Designation, Employee, AttendanceRecord,
    LeaveType, LeaveBalance, LeaveApplication,
    PayrollRun, Payslip, EmployeeAllowanceBonus,
    SalaryStructure, SalaryComponent, EmployeeSalaryAssignment
)

User = get_user_model()


class HREnhancementsTests(APITestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_enh',
            email='tenant_enh@example.com',
            password='password123',
            business_name='Tenant Corp',
            role='admin'
        )
        self.client.force_authenticate(user=self.tenant)

        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')

        self.employee = Employee.objects.create(
            tenant=self.tenant,
            full_name='Test Employee',
            date_of_birth=datetime.date(1996, 5, 10),
            date_of_joining=datetime.date(2025, 3, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Maharashtra',
            personal_phone='9876543210',
            bank_name='SBI',
            bank_account_number='1234567890',
            bank_ifsc='SBIN0001234',
            account_holder_name='Test Employee'
        )

    def test_attendance_before_joining_date_rejected(self):
        """Attendance date cannot be prior to employee date_of_joining."""
        url = reverse('attendance-list')
        # Before joining date (Feb 28, 2025 vs joining March 1, 2025)
        payload = {
            'employee': str(self.employee.id),
            'date': '2025-02-28',
            'status': 'present'
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date', res.data)

        # On joining date (March 1, 2025)
        payload['date'] = '2025-03-01'
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_bulk_attendance_skips_before_joining_date(self):
        """Bulk attendance skips dates earlier than joining date."""
        url = reverse('bulk-attendance')
        records = [
            {'employee_id': str(self.employee.id), 'date': '2025-02-27', 'status': 'present'},
            {'employee_id': str(self.employee.id), 'date': '2025-03-02', 'status': 'present'},
        ]
        res = self.client.post(url, records, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['created'], 1)
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.employee, date='2025-02-27').exists())
        self.assertTrue(AttendanceRecord.objects.filter(employee=self.employee, date='2025-03-02').exists())

    def test_hr_reports_all_types_return_200(self):
        """Test HRReportsView for payroll_register, department_expenses, statutory_summary without 500 error."""
        pr = PayrollRun.objects.create(
            tenant=self.tenant, month=3, year=2025, status='approved',
            total_gross=Decimal('50000.00'), total_net=Decimal('45000.00')
        )
        Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=pr,
            employee=self.employee,
            present_days=Decimal('25.0'),
            total_working_days=25,
            gross_salary=Decimal('50000.00'),
            net_salary=Decimal('45000.00'),
            employee_pf=Decimal('1800.00'),
            employee_esi=Decimal('0.00'),
            professional_tax=Decimal('200.00'),
            tds=Decimal('3000.00'),
            employer_pf=Decimal('1800.00'),
            employer_esi=Decimal('0.00'),
            employer_total_contribution=Decimal('1800.00'),
            total_deductions=Decimal('5000.00')
        )

        url = reverse('hr-reports')
        # 1. payroll_register
        r1 = self.client.get(url, {'type': 'payroll_register', 'year': 2025, 'month': 3})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r1.data['records']), 1)
        rec = r1.data['records'][0]
        self.assertEqual(rec['working_days'], '25')
        self.assertIn('payslip_id', rec)
        self.assertIn('employee_id', rec)
        self.assertIn('designation', rec)
        self.assertIn('earnings', rec)
        self.assertIn('deduction_reasons', rec)
        self.assertIn('pan', rec)
        self.assertIn('uan', rec)
        self.assertIn('tax_regime', rec)

        # 2. department_expenses
        r2 = self.client.get(url, {'type': 'department_expenses', 'year': 2025, 'month': 3})
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertTrue(len(r2.data['results']) >= 1)

        # 3. statutory_summary
        r3 = self.client.get(url, {'type': 'statutory_summary', 'year': 2025, 'month': 3})
        self.assertEqual(r3.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(r3.data['employee_pf']), Decimal('1800.00'))

    def test_leave_quota_exhaustion_blocks_paid_leave(self):
        """Paid leave cannot be applied once employee quota is exhausted."""
        paid_lt = LeaveType.objects.create(
            tenant=self.tenant,
            name='Paid Vacation',
            annual_entitlement=Decimal('2.0'),
            is_paid=True
        )
        unpaid_lt = LeaveType.objects.create(
            tenant=self.tenant,
            name='Leave Without Pay',
            annual_entitlement=Decimal('0.0'),
            is_paid=False
        )

        url = reverse('leave-application-list')

        # Requesting 4 days when entitlement is 2 days should fail
        # 2025-03-03 (Monday) to 2025-03-06 (Thursday) = 4 days
        res_fail = self.client.post(url, {
            'employee': str(self.employee.id),
            'leave_type': str(paid_lt.id),
            'start_date': '2025-03-03',
            'end_date': '2025-03-06',
            'reason': 'Trip'
        })
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('end_date', res_fail.data)

        # Requesting 2 days should succeed
        # 2025-03-03 (Monday) to 2025-03-04 (Tuesday) = 2 days
        res_ok = self.client.post(url, {
            'employee': str(self.employee.id),
            'leave_type': str(paid_lt.id),
            'start_date': '2025-03-03',
            'end_date': '2025-03-04',
            'reason': 'Trip'
        })
        self.assertEqual(res_ok.status_code, status.HTTP_201_CREATED)

        # Approve it to exhaust the 2 days balance
        app_id = res_ok.data['id']
        app_res = self.client.post(reverse('leave-approve', args=[app_id]))
        self.assertEqual(app_res.status_code, status.HTTP_200_OK)

        # Now attempting another application for paid_lt should fail (balance is 0)
        res_exhausted = self.client.post(url, {
            'employee': str(self.employee.id),
            'leave_type': str(paid_lt.id),
            'start_date': '2025-03-05',
            'end_date': '2025-03-05',
            'reason': 'Extra day'
        })
        self.assertEqual(res_exhausted.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('leave_type', res_exhausted.data)

        # But unpaid leave can still be requested
        res_unpaid = self.client.post(url, {
            'employee': str(self.employee.id),
            'leave_type': str(unpaid_lt.id),
            'start_date': '2025-03-05',
            'end_date': '2025-03-05',
            'reason': 'Unpaid day'
        })
        self.assertEqual(res_unpaid.status_code, status.HTTP_201_CREATED)

    def test_employee_allowance_bonus_crud_and_payroll(self):
        """Test creating allowance/bonus and its automatic calculation in payroll."""
        ab = EmployeeAllowanceBonus.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            record_type='bonus',
            title='Festival Bonus',
            amount=Decimal('5000.00'),
            effective_date=datetime.date(2025, 3, 15),
            status='approved'
        )

        recurring_allowance = EmployeeAllowanceBonus.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            record_type='allowance',
            title='Travel Allowance',
            amount=Decimal('2000.00'),
            is_recurring=True,
            status='approved'
        )

        # API check
        url = reverse('allowance-bonus-list')
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['results'] if 'results' in res.data else res.data), 2)

        # Payroll calculation check
        struct = SalaryStructure.objects.create(tenant=self.tenant, name='Standard Dev')
        basic_comp = SalaryComponent.objects.create(
            salary_structure=struct, name='Basic', component_type='fixed', value=Decimal('30000.00'), is_basic=True
        )
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            salary_structure=struct,
            monthly_ctc=Decimal('30000.00'),
            effective_from=datetime.date(2025, 3, 1),
            computed_components={'Basic': '30000.00'}
        )

        pr = PayrollRun.objects.create(tenant=self.tenant, month=3, year=2025, status='draft')
        from hr.services.payroll_engine import compute_payslip_for_employee
        payslip = compute_payslip_for_employee(self.employee, pr)
        self.assertIsNotNone(payslip)

        # 30,000 (basic) + 5,000 (Festival Bonus) + 2,000 (Travel Allowance) = 37,000 gross
        self.assertEqual(payslip.gross_salary, Decimal('37000.00'))
        self.assertIn('Bonus: Festival Bonus', payslip.earnings)
        self.assertIn('Allowance: Travel Allowance', payslip.earnings)
        self.assertEqual(payslip.earnings['Bonus: Festival Bonus'], '5000.00')
        self.assertEqual(payslip.earnings['Allowance: Travel Allowance'], '2000.00')

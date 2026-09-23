from decimal import Decimal
import datetime
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from users.models import User
from hr.models import (
    Employee, Department, Designation, SalaryStructure, SalaryComponent,
    EmployeeSalaryAssignment, PayrollRun, Payslip, OvertimeRecord,
    EmployeeAdvanceLoan, AttendanceRecord, LeaveType, LeaveBalance,
    LeaveApplication, LoanRecoveryLog
)
from hr.services.payroll_engine import (
    compute_payslip_for_employee, compute_pf, run_payroll, apply_rounding
)
from hr.services.hr_accounting_service import HRAccountingService


class ComprehensivePayrollFixesTestCase(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='corp_tenant',
            email='corp@cenvora.com',
            password='password123',
            role='admin',
            business_name='Acme Corp'
        )
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Software Engineer')

        # Employee 1
        self.user_emp1 = User.objects.create_user(
            username='emp_john',
            email='john@example.com',
            password='password123',
            role='employee',
            parent=self.tenant
        )
        self.emp1 = Employee.objects.create(
            tenant=self.tenant,
            user=self.user_emp1,
            employee_code='EMP-001',
            full_name='John Doe',
            date_of_birth='1995-05-15',
            date_of_joining='2023-01-01',
            gender='M',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            personal_email='john@example.com',
            personal_phone='9876543210',
            bank_name='HDFC Bank',
            bank_account_number='50100234567890',
            bank_ifsc='HDFC0001234',
            account_holder_name='John Doe',
            work_state='Delhi'
        )

        # Employee 2 (Coworker)
        self.user_emp2 = User.objects.create_user(
            username='emp_jane',
            email='jane@example.com',
            password='password123',
            role='employee',
            parent=self.tenant
        )
        self.emp2 = Employee.objects.create(
            tenant=self.tenant,
            user=self.user_emp2,
            employee_code='EMP-002',
            full_name='Jane Smith',
            date_of_birth='1994-08-20',
            date_of_joining='2023-01-01',
            gender='F',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            personal_email='jane@example.com',
            bank_name='ICICI Bank',
            bank_account_number='001105001234',
            bank_ifsc='ICIC0000011',
            account_holder_name='Jane Smith',
            work_state='Delhi'
        )

        # Salary Structure with Basic, HRA, and a Deduction component (Canteen)
        self.struct = SalaryStructure.objects.create(tenant=self.tenant, name='Standard Tech')
        self.comp_basic = SalaryComponent.objects.create(
            salary_structure=self.struct,
            name='Basic',
            type='earning',
            component_type='pct_ctc',
            value=Decimal('40.00'),
            is_basic=True
        )
        self.comp_hra = SalaryComponent.objects.create(
            salary_structure=self.struct,
            name='HRA',
            type='earning',
            component_type='pct_basic',
            value=Decimal('50.00'),
            is_basic=False
        )
        self.comp_canteen = SalaryComponent.objects.create(
            salary_structure=self.struct,
            name='Canteen Deduction',
            type='deduction',
            component_type='fixed',
            value=Decimal('1000.00'),
            is_basic=False
        )

        # Assign Salary (CTC = 50,000)
        self.assign1 = EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant,
            employee=self.emp1,
            salary_structure=self.struct,
            effective_from=datetime.date(2023, 1, 1),
            monthly_ctc=Decimal('50000.00'),
            computed_components={
                'Basic': '20000.00',
                'HRA': '10000.00',
                'Canteen Deduction': '1000.00'
            }
        )

        self.assign2 = EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant,
            employee=self.emp2,
            salary_structure=self.struct,
            effective_from=datetime.date(2023, 1, 1),
            monthly_ctc=Decimal('50000.00'),
            computed_components={
                'Basic': '20000.00',
                'HRA': '10000.00',
            }
        )

        self.payroll_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=10,
            year=2023,
            status='draft'
        )

    def test_deduction_component_subtracted_not_added(self):
        """Verify that components with type='deduction' reduce net salary and are not added to gross."""
        payslip = compute_payslip_for_employee(self.emp1, self.payroll_run)
        self.assertIsNotNone(payslip)

        # Gross should be sum of earnings only (Basic 20k + HRA 10k + Special Allowance balancing 20k = 50k)
        # It must NOT include Canteen Deduction in gross_salary
        self.assertEqual(payslip.gross_salary, Decimal('50000.00'))
        self.assertIn('Canteen Deduction', payslip.deductions)
        self.assertEqual(payslip.deductions['Canteen Deduction'], '1000.00')

        # Net salary should subtract Canteen Deduction and statutory deductions
        # Gross (50,000) - Deductions (PF: 2,400 + TDS: 2,817 + Canteen: 1,000 = 6,217) = 43,783
        self.assertEqual(payslip.employee_pf, Decimal('2400.00'))
        self.assertEqual(payslip.total_deductions, Decimal('6217.00'))
        self.assertEqual(payslip.net_salary, Decimal('43783.00'))

        # Check deduction reason transparency
        self.assertIn('Canteen Deduction', payslip.deduction_reasons)
        self.assertIn('Standard Tech', payslip.deduction_reasons['Canteen Deduction']['reason'])

    def test_unpaid_leave_lop_docking(self):
        """Verify that unpaid leaves increment LOP and dock salary."""
        unpaid_type = LeaveType.objects.create(
            tenant=self.tenant,
            name='Unpaid Sabbatical',
            annual_entitlement=Decimal('0.0'),
            is_paid=False
        )
        # Apply and approve 2 days unpaid leave in Oct 2023 (Oct 16 and Oct 17)
        app = LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.emp1,
            leave_type=unpaid_type,
            start_date=datetime.date(2023, 10, 16),
            end_date=datetime.date(2023, 10, 17),
            computed_days=Decimal('2.0'),
            status='approved'
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=self.emp1,
            date=datetime.date(2023, 10, 16), status='leave'
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=self.emp1,
            date=datetime.date(2023, 10, 17), status='leave'
        )

        payslip = compute_payslip_for_employee(self.emp1, self.payroll_run)
        self.assertIsNotNone(payslip)
        self.assertEqual(payslip.lop_days, Decimal('2.0'))
        self.assertEqual(payslip.present_days, Decimal('24.0'))  # 26 working days - 2 LOP
        self.assertIn('Loss of Pay (LOP)', payslip.deduction_reasons)
        self.assertIn('Unpaid leave on: 2023-10-16', payslip.deduction_reasons['Loss of Pay (LOP)']['reason'])

    def test_mid_month_joiner_preserves_post_joining_absences(self):
        """Verify mid-month joiners have pre-joining days counted as LOP without wiping out post-joining absences."""
        mid_joiner = Employee.objects.create(
            tenant=self.tenant,
            employee_code='EMP-003',
            full_name='Mid Joiner',
            date_of_birth='1996-01-01',
            date_of_joining='2023-10-10',  # Joined Oct 10
            gender='M',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Delhi'
        )
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant,
            employee=mid_joiner,
            salary_structure=self.struct,
            effective_from=datetime.date(2023, 10, 10),
            monthly_ctc=Decimal('50000.00'),
            computed_components={'Basic': '20000.00', 'HRA': '10000.00'}
        )
        # Mark 1 absent day after joining on Oct 20
        AttendanceRecord.objects.create(
            tenant=self.tenant, employee=mid_joiner,
            date=datetime.date(2023, 10, 20), status='absent'
        )

        payslip = compute_payslip_for_employee(mid_joiner, self.payroll_run)
        self.assertIsNotNone(payslip)
        # Working days before Oct 10 = 7 days. Logged absence = 1 day. Total LOP = 8 days.
        # Oct 2023 has 26 working days. Present days = 26 - 8 = 18 days.
        self.assertEqual(payslip.lop_days, Decimal('8.0'))
        self.assertEqual(payslip.present_days, Decimal('18.0'))
        self.assertIn('2023-10-20', payslip.deduction_reasons['Loss of Pay (LOP)']['reason'])

    def test_overtime_auto_calculation(self):
        """Verify OvertimeRecord automatically calculates amount on save and includes in payslip."""
        ot = OvertimeRecord.objects.create(
            tenant=self.tenant,
            employee=self.emp1,
            date=datetime.date(2023, 10, 5),
            hours=Decimal('10.0'),
            hourly_rate=Decimal('200.00'),
            multiplier=Decimal('1.5'),
            status='approved'
        )
        self.assertEqual(ot.amount, Decimal('3000.00'))  # 10 * 200 * 1.5

        payslip = compute_payslip_for_employee(self.emp1, self.payroll_run)
        self.assertEqual(payslip.overtime_amount, Decimal('3000.00'))
        self.assertIn('Overtime', payslip.earnings)
        self.assertEqual(payslip.earnings['Overtime'], '3000.00')

    def test_loan_recovery_reversal_restores_balance(self):
        """Verify that reopening a payroll run restores loan balances and resets status."""
        loan = EmployeeAdvanceLoan.objects.create(
            tenant=self.tenant,
            employee=self.emp1,
            record_type='advance',
            original_amount=Decimal('5000.00'),
            outstanding_balance=Decimal('5000.00'),
            monthly_installment=Decimal('2500.00'),
            disbursement_date=datetime.date(2023, 9, 1),
            status='active'
        )

        run_payroll(str(self.payroll_run.id))
        self.payroll_run.refresh_from_db()
        self.payroll_run.status = 'approved'
        self.payroll_run.save()

        # Simulate pay
        HRAccountingService.post_payroll_disbursement(self.payroll_run, None, self.tenant)
        loan.refresh_from_db()
        self.assertEqual(loan.outstanding_balance, Decimal('2500.00'))
        self.assertTrue(LoanRecoveryLog.objects.filter(payslip__payroll_run=self.payroll_run).exists())

        # Reopen payroll
        HRAccountingService.reverse_payroll_accrual(self.payroll_run, self.tenant, "Audited Correction")
        loan.refresh_from_db()
        # Outstanding balance must be restored back to 5000!
        self.assertEqual(loan.outstanding_balance, Decimal('5000.00'))
        self.assertEqual(loan.status, 'active')

    def test_epfo_eps_statutory_cap(self):
        """Verify statutory 8.33% EPS contribution is capped at ₹1,250 with excess credited to EPF."""
        # High basic salary ₹40,000
        pf_details = compute_pf(Decimal('40000.00'))
        # 12% total employer PF = 4,800
        self.assertEqual(pf_details['employer_pf'], Decimal('4800.00'))
        # EPS capped at 1,250
        self.assertEqual(pf_details['employer_eps'], Decimal('1250.00'))
        # EPF gets remaining 3,550 (4800 - 1250)
        self.assertEqual(pf_details['employer_epf'], Decimal('3550.00'))

    def test_employee_rbac_privacy_guard(self):
        """Verify regular employees cannot view coworkers' payslips or download coworker PDFs."""
        run_payroll(str(self.payroll_run.id))
        ps_emp1 = Payslip.objects.get(payroll_run=self.payroll_run, employee=self.emp1)
        ps_emp2 = Payslip.objects.get(payroll_run=self.payroll_run, employee=self.emp2)

        client = APIClient()
        # Authenticate as Employee 1 (John)
        client.force_authenticate(user=self.user_emp1)

        # 1. Querying /api/hr/payslips/ should only return John's payslip
        resp = client.get('/api/hr/payslips/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get('results', resp.data)
        ids = [item['id'] for item in results]
        self.assertIn(str(ps_emp1.id), ids)
        self.assertNotIn(str(ps_emp2.id), ids)

        # 2. Downloading John's PDF is allowed (200 OK)
        pdf_resp = client.get(f'/api/hr/payslips/{ps_emp1.id}/pdf/')
        self.assertEqual(pdf_resp.status_code, status.HTTP_200_OK)

        # 3. Downloading Jane's PDF is forbidden (403 Forbidden)
        pdf_resp_coworker = client.get(f'/api/hr/payslips/{ps_emp2.id}/pdf/')
        self.assertEqual(pdf_resp_coworker.status_code, status.HTTP_403_FORBIDDEN)

    def test_bank_payout_csv_export(self):
        """Verify bank_export action on PayrollRun returns a formatted CSV."""
        run_payroll(str(self.payroll_run.id))
        client = APIClient()
        client.force_authenticate(user=self.tenant)

        resp = client.get(f'/api/hr/payroll-runs/{self.payroll_run.id}/bank_export/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        content = resp.content.decode('utf-8')
        self.assertIn('Beneficiary Account Number', content)
        self.assertIn('50100234567890', content)  # John's account
        self.assertIn('HDFC0001234', content)

    def test_reopen_approved_and_locked_payroll_runs(self):
        """Verify that an approved or locked payroll run can be reopened via API action."""
        run_payroll(str(self.payroll_run.id))
        self.payroll_run.refresh_from_db()
        self.payroll_run.status = 'approved'
        self.payroll_run.save()

        client = APIClient()
        client.force_authenticate(user=self.tenant)

        # 1. Reopening an approved run should succeed (200 OK)
        resp = client.post(f'/api/hr/payroll-runs/{self.payroll_run.id}/reopen/', {'reason': 'Leave adjust requested'})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.payroll_run.refresh_from_db()
        self.assertEqual(self.payroll_run.status, 'draft')
        self.assertTrue(self.payroll_run.is_reopened)
        self.assertEqual(self.payroll_run.reopen_reason, 'Leave adjust requested')
        self.assertIsNone(self.payroll_run.approved_at)

        # 2. Lock and reopen locked run
        self.payroll_run.status = 'locked'
        self.payroll_run.save()
        resp2 = client.post(f'/api/hr/payroll-runs/{self.payroll_run.id}/reopen/', {'reason': 'Manager revision'})
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.payroll_run.refresh_from_db()
        self.assertEqual(self.payroll_run.status, 'draft')
        self.assertIsNone(self.payroll_run.locked_at)

        # 3. Missing reason raises validation error (400 Bad Request)
        self.payroll_run.status = 'approved'
        self.payroll_run.save()
        resp3 = client.post(f'/api/hr/payroll-runs/{self.payroll_run.id}/reopen/', {'reason': '   '})
        self.assertEqual(resp3.status_code, status.HTTP_400_BAD_REQUEST)

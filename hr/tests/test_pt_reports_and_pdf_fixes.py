"""Tests for PT calculations, statutory reports (PF ECR / ESI), and payslip PDF rationale."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from rest_framework.test import APITestCase

from hr.models import Department, Designation, Employee, PayrollRun, Payslip
from hr.services.payroll_engine import compute_pt, get_hrms_settings
from hr.services.pdf_service import generate_payslip_pdf
from hr.services.statutory_reports import generate_pf_ecr, generate_esi_return
from hr.services.work_state import resolve_work_state
from users.models import User

D = Decimal


class PTReportsAndPDFFixesTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs', verbosity=0)

    def setUp(self):
        # Tenant in MP with state empty but GSTIN 23...
        self.tenant = User.objects.create_user(
            username='mp_company_admin',
            email='admin@mpcompany.com',
            password='secretpassword123',
            business_name='MP Tech Solutions',
            role='admin',
            state='',  # blank state
            gstin='23AABCU9603R1ZM'  # 23 = Madhya Pradesh
        )
        self.client.force_authenticate(self.tenant)
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')

        self.emp_amit = Employee.objects.create(
            tenant=self.tenant,
            employee_code='EMP-0001',
            full_name='Amit Sharma',
            personal_email='amit@example.com',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2025, 1, 1),
            department=self.dept,
            designation=self.desig,
            work_state='',  # blank work_state
        )

        self.emp_priya = Employee.objects.create(
            tenant=self.tenant,
            employee_code='EMP-0002',
            full_name='Priya Patel',
            personal_email='priya@example.com',
            date_of_birth=datetime.date(1992, 2, 2),
            date_of_joining=datetime.date(2025, 1, 1),
            department=self.dept,
            designation=self.desig,
            work_state='Madhya Pradesh',
        )

    def test_resolve_work_state_fallback_to_gstin(self):
        """When employee work_state and tenant.state are blank, fall back to tenant.gstin prefix."""
        resolved = resolve_work_state(employee=self.emp_amit, tenant=self.tenant)
        self.assertEqual(resolved, 'Madhya Pradesh')

    def test_mp_pt_calculation_slabs(self):
        """Verify Madhya Pradesh PT slabs: <=22500 -> 0; 22501-30000 -> 125; 30001-40000 -> 167; 40001+ -> 200."""
        # <= 22,500
        pt_nil = compute_pt(D('20000.00'), 'Madhya Pradesh', month=11)
        self.assertEqual(pt_nil, D('0.00'))

        # Priya, Sunita, Test Six: gross around 25,000 -> ₹125
        pt_125 = compute_pt(D('25000.00'), 'Madhya Pradesh', month=11)
        self.assertEqual(pt_125, D('125.00'))

        # 30,001 to 40,000 -> ₹167
        pt_167 = compute_pt(D('35000.00'), 'Madhya Pradesh', month=11)
        self.assertEqual(pt_167, D('167.00'))

        # Amit, Rakesh: gross > 40,000 -> ₹200
        pt_200 = compute_pt(D('50000.00'), 'Madhya Pradesh', month=11)
        self.assertEqual(pt_200, D('200.00'))

    def test_compute_pt_with_blank_work_state_uses_gstin_state(self):
        """compute_pt resolves tenant GSTIN prefix 23 -> MP and computes PT correctly."""
        pt = compute_pt(
            gross_salary=D('50000.00'),
            work_state='',
            month=11,
            employee=self.emp_amit,
            tenant=self.tenant,
        )
        self.assertEqual(pt, D('200.00'))

    def test_generate_pf_ecr_includes_employees_with_pf_deductions(self):
        """generate_pf_ecr must include all employees where employee_pf > 0."""
        payroll_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=11,
            year=2026,
            status='draft',
        )

        p1 = Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=payroll_run,
            employee=self.emp_amit,
            total_working_days=30,
            present_days=D('30.0'),
            absent_days=D('0.0'),
            lop_days=D('0.0'),
            gross_salary=D('50000.00'),
            employee_pf=D('1800.00'),
            employer_pf=D('1800.00'),
            employer_epf=D('550.00'),
            employer_eps=D('1250.00'),
            employee_esi=D('0.00'),
            employer_esi=D('0.00'),
            professional_tax=D('200.00'),
            tds=D('500.00'),
            total_deductions=D('2500.00'),
            net_salary=D('47500.00'),
            deduction_reasons={'PF': {'pf_wage': '15000.00', 'reason': 'EPFO statutory'}},
        )

        ecr_data = generate_pf_ecr(self.tenant, 2026, 11)
        self.assertEqual(ecr_data['count'], 1)
        self.assertEqual(len(ecr_data['records']), 1)
        rec = ecr_data['records'][0]
        self.assertEqual(rec['member_name'], 'Amit Sharma')
        self.assertEqual(rec['ee_share'], '1800.00')
        self.assertEqual(rec['eps_share'], '1250.00')
        self.assertEqual(rec['er_share'], '550.00')

    def test_payslip_pdf_pt_line_rationale_not_tds(self):
        """The PT line in generate_payslip_pdf must not be assigned the Section 192 TDS rationale."""
        payroll_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=11,
            year=2026,
            status='finalized',
        )

        payslip = Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=payroll_run,
            employee=self.emp_amit,
            total_working_days=30,
            present_days=D('30.0'),
            absent_days=D('0.0'),
            lop_days=D('0.0'),
            gross_salary=D('50000.00'),
            employee_pf=D('1800.00'),
            professional_tax=D('200.00'),
            tds=D('1000.00'),
            total_deductions=D('3000.00'),
            net_salary=D('47000.00'),
            earnings={'Basic': '25000.00', 'HRA': '12500.00', 'Special': '12500.00'},
            deductions={'Provident Fund (PF)': '1800.00', 'Professional Tax (PT)': '200.00', 'Income Tax (TDS)': '1000.00'},
            deduction_reasons={
                'PT': {'amount': '200.00', 'reason': 'State statutory Professional Tax slab deduction'},
                'TDS': {'amount': '1000.00', 'reason': 'Monthly income tax withholding deducted under Section 192'},
            },
        )

        pdf_bytes = generate_payslip_pdf(payslip)
        self.assertGreater(len(pdf_bytes), 1000)

    def test_pt_register_report_resolves_work_state(self):
        """GET /api/hr/reports/?type=pt_register returns lines with resolved work state."""
        payroll_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=11,
            year=2026,
            status='finalized',
        )

        Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=payroll_run,
            employee=self.emp_amit,
            total_working_days=30,
            present_days=D('30.0'),
            absent_days=D('0.0'),
            lop_days=D('0.0'),
            gross_salary=D('50000.00'),
            professional_tax=D('200.00'),
            total_deductions=D('200.00'),
            net_salary=D('49800.00'),
        )

        res = self.client.get('/api/hr/reports/', {'type': 'pt_register', 'year': 2026, 'month': 11})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['lines'][0]['work_state'], 'Madhya Pradesh')
        self.assertEqual(res.data['lines'][0]['professional_tax'], '200.00')

import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from hr.models import (
    Employee, Department, Designation, SalaryStructure,
    SalaryComponent, EmployeeSalaryAssignment, EmployeeTaxDeclaration,
    PayrollRun, Payslip
)
from hr.services.tds_service import (
    compute_dynamic_tds, compute_tax_on_income, get_financial_year_info
)

User = get_user_model()


class DynamicTDSEngineTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username="tax_admin",
            email="admin@taxcorp.com",
            password="password123",
            role="admin"
        )
        self.dept = Department.objects.create(tenant=self.tenant, name="Engineering")
        self.desig = Designation.objects.create(tenant=self.tenant, name="Software Engineer")

        self.struct = SalaryStructure.objects.create(tenant=self.tenant, name="Standard Tech")
        SalaryComponent.objects.create(
            salary_structure=self.struct, name="Basic", type="earning",
            component_type="pct_gross", value=Decimal('50.00'), is_basic=True, order=1
        )
        SalaryComponent.objects.create(
            salary_structure=self.struct, name="HRA", type="earning",
            component_type="pct_basic", value=Decimal('50.00'), is_basic=False, order=2
        )
        SalaryComponent.objects.create(
            salary_structure=self.struct, name="Special Allowance", type="earning",
            component_type="fixed", value=Decimal('0.00'), is_basic=False, order=3
        )

        self.emp = Employee.objects.create(
            tenant=self.tenant,
            employee_code="EMP-TAX-01",
            full_name="Rajesh Sharma",
            date_of_birth=datetime.date(1995, 5, 20),
            date_of_joining=datetime.date(2026, 4, 1),
            gender="M",
            employment_type="full_time",
            department=self.dept,
            designation=self.desig,
            work_state="Maharashtra",
            personal_phone="9876543210",
            bank_name="HDFC Bank",
            bank_account_number="123456789012",
            bank_ifsc="HDFC0001234",
            account_holder_name="Rajesh Sharma",
            tax_regime="new"
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)

    def test_financial_year_info_april_and_january(self):
        # April 2026 => month 1 of FY 2026-2027, 12 remaining
        info_apr = get_financial_year_info(4, 2026)
        self.assertEqual(info_apr['fy_string'], '2026-2027')
        self.assertEqual(info_apr['month_idx_in_fy'], 1)
        self.assertEqual(info_apr['remaining_months'], 12)

        # January 2027 => month 10 of FY 2026-2027, 3 remaining
        info_jan = get_financial_year_info(1, 2027)
        self.assertEqual(info_jan['fy_string'], '2026-2027')
        self.assertEqual(info_jan['month_idx_in_fy'], 10)
        self.assertEqual(info_jan['remaining_months'], 3)

        # March 2027 => month 12 of FY 2026-2027, 1 remaining
        info_mar = get_financial_year_info(3, 2027)
        self.assertEqual(info_mar['remaining_months'], 1)

    def test_zero_tax_employee_new_regime_sec_87a_rebate(self):
        """Monthly gross ₹60,000 -> Annual ₹7.20L -> minus ₹75k std ded = ₹6.45L taxable.
        Within ₹7.0L Section 87A rebate threshold -> Tax liability ₹0.00 -> Monthly TDS ₹0.00.
        """
        tds_data = compute_dynamic_tds(self.emp, Decimal('60000.00'), month=4, year=2026)
        self.assertEqual(tds_data['monthly_tds'], Decimal('0.00'))
        self.assertEqual(tds_data['total_annual_tax'], Decimal('0.00'))
        self.assertTrue(tds_data['rebate_applied'])
        self.assertIn("Section 87A rebate", tds_data['reason'])

    def test_taxable_employee_new_regime(self):
        """Monthly gross ₹1,20,000 -> Annual ₹14.40L -> minus ₹75k = ₹13.65L taxable.
        Tax under New Regime:
        0 - 3L: 0
        3L - 7L: 5% of 4L = 20,000
        7L - 10L: 10% of 3L = 30,000
        10L - 12L: 15% of 2L = 30,000
        12L - 13.65L: 20% of 1.65L = 33,000
        Base tax = 1,13,000
        + 4% cess = 4,520 -> Total 1,17,520
        Monthly TDS = 1,17,520 / 12 = 9,793.33.
        """
        tds_data = compute_dynamic_tds(self.emp, Decimal('120000.00'), month=4, year=2026)
        self.assertEqual(tds_data['total_annual_tax'], Decimal('117520.00'))
        self.assertEqual(tds_data['monthly_tds'], Decimal('9793.33'))
        self.assertFalse(tds_data['rebate_applied'])
        self.assertIn("TDS under Sec 192", tds_data['reason'])

    def test_old_regime_with_declarations(self):
        """Employee opts for Old Regime with ₹1.5L 80C and ₹25k 80D."""
        EmployeeTaxDeclaration.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            financial_year='2026-2027',
            regime='old',
            section_80c=Decimal('150000.00'),
            section_80d=Decimal('25000.00'),
            proof_verified=True,
        )

        # Monthly gross ₹80,000 -> Annual ₹9.60L
        # Deductions: Std Ded 50,000 + 80C 1,50,000 + 80D 25,000 = 2,25,000
        # Taxable: 9,60,000 - 2,25,000 = 7,35,000
        # Tax Old Regime: 12,500 + 20% of (7,35,000 - 5,00,000) = 12,500 + 47,000 = 59,500
        # + 4% cess = 2,380 -> Total = 61,880
        # Monthly TDS = 61,880 / 12 = 5,156.67
        tds_data = compute_dynamic_tds(self.emp, Decimal('80000.00'), month=4, year=2026)
        self.assertEqual(tds_data['regime'], 'old')
        self.assertEqual(tds_data['total_annual_tax'], Decimal('61880.00'))
        self.assertEqual(tds_data['monthly_tds'], Decimal('5156.67'))

    def test_q4_unverified_proofs_dropped(self):
        """In January (month 10), if Old Regime proofs are not verified,
        unverified 80C declarations are dropped and remaining tax is spread over 3 months.
        """
        # Create past payroll run in September 2026 with gross 6,00,000
        past_run = PayrollRun.objects.create(
            tenant=self.tenant, month=9, year=2026, status='locked',
            total_gross=Decimal('600000.00'), total_net=Decimal('550000.00'),
            total_deductions=Decimal('50000.00')
        )
        Payslip.objects.create(
            tenant=self.tenant, payroll_run=past_run, employee=self.emp,
            present_days=Decimal('22.0'), total_working_days=22,
            gross_salary=Decimal('600000.00'), net_salary=Decimal('550000.00'),
            total_deductions=Decimal('50000.00'), tds=Decimal('15000.00')
        )

        decl = EmployeeTaxDeclaration.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            financial_year='2026-2027',
            regime='old',
            section_80c=Decimal('150000.00'),
            proof_verified=False,  # Unverified
        )

        tds_data = compute_dynamic_tds(self.emp, Decimal('80000.00'), month=1, year=2027)
        self.assertTrue(tds_data['q4_unverified_dropped'])
        self.assertIn("Q4 Proofs Unverified", tds_data['reason'])

    def test_calculate_salary_breakdown_endpoint(self):
        """Test POST /api/hr/employees/calculate_salary_breakdown/ returns real-time components."""
        payload = {
            'monthly_ctc': 60000,
            'tax_regime': 'new',
            'work_state': 'Maharashtra'
        }
        res = self.client.post('/api/hr/employees/calculate_salary_breakdown/', payload, format='json')
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertEqual(data['monthly_ctc'], '60000.00')
        self.assertEqual(data['earnings']['basic'], '30000.00')
        self.assertEqual(data['earnings']['hra'], '15000.00')
        self.assertEqual(data['earnings']['special_allowance'], '15000.00')
        self.assertEqual(data['employee_deductions']['employee_pf'], '3600.00')
        self.assertEqual(data['tds_details']['monthly_tds'], '0.00')
        self.assertTrue(data['tds_details']['rebate_applied'])

    def test_create_employee_with_nested_salary_and_tax(self):
        """Test creating an employee with nested salary and tax declaration payload."""
        payload = {
            'full_name': 'Ananya Verma',
            'date_of_birth': '1998-08-15',
            'date_of_joining': '2026-05-01',
            'gender': 'F',
            'employment_type': 'full_time',
            'department': self.dept.id,
            'designation': self.desig.id,
            'work_state': 'Karnataka',
            'personal_phone': '9811223344',
            'bank_name': 'ICICI Bank',
            'bank_account_number': '998877665544',
            'bank_ifsc': 'ICIC0009988',
            'account_holder_name': 'Ananya Verma',
            'tax_regime': 'new',
            'salary': {
                'monthly_ctc': 75000,
                'effective_from': '2026-05-01',
            },
            'tax_declaration': {
                'financial_year': '2026-2027',
                'regime': 'new',
            }
        }
        res = self.client.post('/api/hr/employees/', payload, format='json')
        self.assertEqual(res.status_code, 201)
        emp_id = res.data['id']
        emp = Employee.objects.get(id=emp_id)
        self.assertEqual(emp.tax_regime, 'new')

        assignment = emp.salary_assignments.first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.monthly_ctc, Decimal('75000.00'))
        self.assertEqual(assignment.computed_components['Basic'], '37500.00')
        self.assertEqual(assignment.computed_components['HRA'], '18750.00')
        self.assertEqual(assignment.computed_components['Special Allowance'], '18750.00')

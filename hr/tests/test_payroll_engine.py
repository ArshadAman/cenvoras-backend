from decimal import Decimal
from django.test import TestCase
from hr.services.payroll_engine import compute_pf, compute_esi, compute_tds, compute_pt
from hr.models import ProfessionalTaxSlab

class PayrollEngineTests(TestCase):
    def test_compute_pf(self):
        basic = Decimal('10000.00')
        pf_details = compute_pf(basic)
        
        self.assertEqual(pf_details['employee_pf'], Decimal('1200.00'))
        self.assertEqual(pf_details['employer_pf'], Decimal('1200.00'))
        self.assertEqual(pf_details['employer_epf'], Decimal('367.00'))
        self.assertEqual(pf_details['employer_eps'], Decimal('833.00'))

    def test_compute_esi_under_threshold(self):
        gross = Decimal('20000.00') # <= 21000
        esi_details = compute_esi(gross)
        
        self.assertEqual(esi_details['employee_esi'], Decimal('150.00')) # 0.75%
        self.assertEqual(esi_details['employer_esi'], Decimal('650.00')) # 3.25%
        
    def test_compute_esi_over_threshold(self):
        gross = Decimal('25000.00') # > 21000
        esi_details = compute_esi(gross)
        
        self.assertEqual(esi_details['employee_esi'], Decimal('0.00'))
        self.assertEqual(esi_details['employer_esi'], Decimal('0.00'))

    def test_compute_tds_zero_tax(self):
        # 40k/mo = 4.8L/yr. Below 5L rebate
        gross = Decimal('40000.00')
        tds = compute_tds(gross)
        self.assertEqual(tds, Decimal('0.00'))

    def test_compute_tds_taxable(self):
        # 100k/mo = 12L/yr.
        # Taxable:
        # 0 to 2.5L = 0
        # 2.5L to 5L = 12,500
        # 5L to 10L = 100,000
        # 10L to 12L = 60,000
        # Total tax = 172,500
        # Cess = 4% of 172,500 = 6,900
        # Total = 179,400
        # Monthly = 14,950.00
        gross = Decimal('100000.00')
        tds = compute_tds(gross)
        self.assertEqual(tds, Decimal('14950.00'))
        
    def test_compute_pt_with_slab(self):
        ProfessionalTaxSlab.objects.create(
            state_name="Maharashtra",
            lower_bound=Decimal("10000.01"),
            upper_bound=None,
            pt_amount=Decimal("200.00")
        )
        
        gross = Decimal('25000.00')
        pt = compute_pt(gross, "Maharashtra")
        self.assertEqual(pt, Decimal('200.00'))
        
    def test_compute_pt_no_slab(self):
        # No slabs for Delhi
        gross = Decimal('25000.00')
        pt = compute_pt(gross, "Delhi")
        self.assertEqual(pt, Decimal('0.00'))

    def test_net_salary_calculation(self):
        from users.models import User
        from hr.models import Employee, SalaryStructure, SalaryComponent, EmployeeSalaryAssignment, PayrollRun, Department, Designation
        import datetime

        tenant = User.objects.create_user(username='tenant2', email='tenant2@example.com', password='pw')
        
        dept = Department.objects.create(tenant=tenant, name='Tech')
        desig = Designation.objects.create(tenant=tenant, name='Engineer')
        
        emp = Employee.objects.create(
            tenant=tenant,
            employee_code='EMP-001',
            full_name='John Doe',
            date_of_birth='1990-01-01',
            date_of_joining='2020-01-01',
            gender='M',
            employment_type='full_time',
            designation=desig,
            department=dept,
            status='active',
            work_state='Delhi'
        )
        
        struct = SalaryStructure.objects.create(tenant=tenant, name='Struct1')
        SalaryComponent.objects.create(salary_structure=struct, name='Basic', component_type='fixed', value='10000.00', is_basic=True)
        SalaryComponent.objects.create(salary_structure=struct, name='HRA', component_type='fixed', value='15000.00', is_basic=False)
        
        assignment = EmployeeSalaryAssignment.objects.create(
            tenant=tenant,
            employee=emp,
            salary_structure=struct,
            effective_from='2023-01-01',
            monthly_ctc=Decimal('25000.00'),
            computed_components={'Basic': '10000.00', 'HRA': '15000.00'}
        )
        
        run = PayrollRun.objects.create(tenant=tenant, month=10, year=2023, status='draft')
        
        # Present for full month. Working days = approx 26, present = approx 26, proration = 1.0
        # Actually we need mock for get_present_days and get_total_working_days to enforce proration = 1.0
        from unittest.mock import patch
        import hr.services.payroll_engine
        with patch('hr.services.payroll_engine.get_present_days', return_value=Decimal('26.0')), \
             patch('hr.services.payroll_engine.get_total_working_days', return_value=26):
             
            payslip = hr.services.payroll_engine.compute_payslip_for_employee(emp, run)
            
            # Gross = 25000
            # Basic = 10000
            # PF = 12% of 10000 = 1200
            # ESI = 0 because gross > 21000
            # TDS = 0 because annual gross is 3L (rebate)
            # PT = 0 because Delhi has no slab
            # Net = 25000 - 1200 = 23800
            self.assertIsNotNone(payslip)
            self.assertEqual(payslip.gross_salary, Decimal('25000.00'))
            self.assertEqual(payslip.employee_pf, Decimal('1200.00'))
            self.assertEqual(payslip.employee_esi, Decimal('0.00'))
            self.assertEqual(payslip.tds, Decimal('0.00'))
            self.assertEqual(payslip.professional_tax, Decimal('0.00'))
            self.assertEqual(payslip.net_salary, Decimal('23800.00'))

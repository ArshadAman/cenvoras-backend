from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from users.models import User
from hr.models import Payslip, PayrollRun

class PayslipPDFViewTests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='password123',
            role='admin'
        )
        self.client.force_authenticate(user=self.tenant)
        
        from hr.models import Employee, Department, Designation
        dept = Department.objects.create(tenant=self.tenant, name='Tech')
        desig = Designation.objects.create(tenant=self.tenant, name='Engineer')
        
        self.employee = Employee.objects.create(
            tenant=self.tenant,
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
        
        self.run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=10,
            year=2023,
            status='finalised'
        )
        
        self.payslip = Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=self.run,
            employee=self.employee,
            present_days=26.0,
            total_working_days=26,
            gross_salary='25000.00',
            net_salary='23800.00'
        )

    def test_generate_pdf_returns_content(self):
        url = reverse('payslip-pdf', kwargs={'pk': self.payslip.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 0)
        
    def test_missing_payslip_returns_404(self):
        import uuid
        url = reverse('payslip-pdf', kwargs={'pk': uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

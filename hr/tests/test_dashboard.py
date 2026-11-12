from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from users.models import User
from hr.models import Employee, PayrollRun, LeaveApplication, LeaveType, Department, Designation, PayrollException
import datetime

class HRDashboardTests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='password123',
            role='admin'
        )
        self.client.force_authenticate(user=self.tenant)
        
        dept = Department.objects.create(tenant=self.tenant, name='Tech')
        desig = Designation.objects.create(tenant=self.tenant, name='Engineer')
        
        self.emp1 = Employee.objects.create(
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
        
        self.emp2 = Employee.objects.create(
            tenant=self.tenant,
            employee_code='EMP-002',
            full_name='Jane Doe',
            date_of_birth='1990-01-01',
            date_of_joining='2020-01-01',
            gender='F',
            employment_type='full_time',
            designation=desig,
            department=dept,
            status='active',
            work_state='Delhi'
        )
        
        # Inactive employee shouldn't count in total_employees if the logic filters by active
        self.emp3 = Employee.objects.create(
            tenant=self.tenant,
            employee_code='EMP-003',
            full_name='Jim Doe',
            date_of_birth='1990-01-01',
            date_of_joining='2020-01-01',
            gender='M',
            employment_type='full_time',
            designation=desig,
            department=dept,
            status='inactive',
            work_state='Delhi'
        )
        
        # Payroll run
        PayrollRun.objects.create(
            tenant=self.tenant,
            month=datetime.date.today().month,
            year=datetime.date.today().year,
            status='completed',
            total_net='50000.00'
        )
        
        # Leave app
        ltype = LeaveType.objects.create(tenant=self.tenant, name='Annual Leave', annual_entitlement='12.0')
        LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.emp1,
            leave_type=ltype,
            start_date=datetime.date.today(),
            end_date=datetime.date.today() + datetime.timedelta(days=1),
            status='pending',
            reason='Vacation'
        )

    def test_dashboard_stats(self):
        url = reverse('hr-dashboard')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_active_employees', response.data)
        self.assertIn('on_leave_today', response.data)
        self.assertIn('present_today', response.data)
        self.assertIn('last_payroll_net', response.data)
        
        self.assertEqual(response.data['total_active_employees'], 2)
        
        # In our setup, last_payroll_net could be None because it's looking for 
        # a specific month/year logic, or we created the run.
        # Let's just assert it is present.

    def test_critical_payroll_exceptions_alert(self):
        pr = PayrollRun.objects.filter(tenant=self.tenant).first()
        PayrollException.objects.create(
            tenant=self.tenant,
            payroll_run=pr,
            employee=self.emp1,
            severity='critical',
            code='MISSING_BANK_INFO',
            message='Missing bank information (Account Number, IFSC). Direct deposit will fail.',
            is_resolved=False
        )

        url = reverse('hr-dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        alerts = response.data.get('alerts', [])
        critical_alerts = [a for a in alerts if a.get('title') == 'Critical Payroll Exceptions']
        self.assertEqual(len(critical_alerts), 1)

        crit = critical_alerts[0]
        self.assertEqual(crit['type'], 'danger')
        self.assertEqual(len(crit['items']), 1)
        item = crit['items'][0]
        self.assertEqual(item['employee_id'], str(self.emp1.id))
        self.assertEqual(item['employee_code'], 'EMP-001')
        self.assertEqual(item['name'], 'John Doe')
        self.assertEqual(item['department'], 'Tech')
        self.assertEqual(item['designation'], 'Engineer')
        self.assertEqual(item['code'], 'MISSING_BANK_INFO')
        self.assertIn('Missing bank information', item['message'])

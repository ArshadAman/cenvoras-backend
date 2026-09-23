"""
Unit tests for Employee ViewSet.

Requirements: 2.3, 2.4, 2.5, 2.7, 12.2
- Test auto-generated EMP-{NNNN} codes are sequential and tenant-scoped
- Test cross-tenant user FK validation returns HTTP 400
- Test inactive employee excluded from active list
- Test cross-tenant isolation returns HTTP 404
"""

import datetime
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee


class EmployeeAPITests(APITestCase):

    def setUp(self):
        # Create Tenant 1 and users
        self.tenant1 = User.objects.create_user(
            username='tenant1_owner',
            email='tenant1@example.com',
            password='testpass',
            business_name='Tenant 1 Corp',
            role='admin'
        )
        self.t1_user = User.objects.create_user(
            username='t1_employee',
            email='t1_emp@example.com',
            password='testpass',
            parent=self.tenant1,
            role='manager'
        )

        # Create Tenant 2 and users
        self.tenant2 = User.objects.create_user(
            username='tenant2_owner',
            email='tenant2@example.com',
            password='testpass',
            business_name='Tenant 2 Corp',
            role='admin'
        )
        self.t2_user = User.objects.create_user(
            username='t2_employee',
            email='t2_emp@example.com',
            password='testpass',
            parent=self.tenant2,
            role='manager'
        )

        # Departments & Designations for T1
        self.dept_t1 = Department.objects.create(tenant=self.tenant1, name='Engineering')
        self.desig_t1 = Designation.objects.create(tenant=self.tenant1, name='Developer')

        # Departments & Designations for T2
        self.dept_t2 = Department.objects.create(tenant=self.tenant2, name='Engineering')
        self.desig_t2 = Designation.objects.create(tenant=self.tenant2, name='Developer')

        self.list_url = reverse('employee-list')

    def test_auto_generated_employee_codes_sequential_and_tenant_scoped(self):
        """Test EMP-{NNNN} codes are sequential and tenant-scoped."""
        self.client.force_authenticate(user=self.tenant1)
        
        # Create Emp 1 in Tenant 1
        payload = {
            'full_name': 'Alice',
            'date_of_birth': '1990-01-01',
            'date_of_joining': '2023-01-01',
            'gender': 'F',
            'employment_type': 'full_time',
            'department': self.dept_t1.id,
            'designation': self.desig_t1.id,
            'work_state': 'Maharashtra',
            'personal_phone': '9876543210',
            'bank_name': 'HDFC Bank',
            'bank_account_number': '1234567890',
            'bank_ifsc': 'HDFC0001234',
            'account_holder_name': 'Alice',
        }
        res1 = self.client.post(self.list_url, payload)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res1.data['employee_code'], 'EMP-0001')

        # Create Emp 2 in Tenant 1
        payload['full_name'] = 'Bob'
        res2 = self.client.post(self.list_url, payload)
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.data['employee_code'], 'EMP-0002')

        # Switch to Tenant 2, sequence should restart at EMP-0001
        self.client.force_authenticate(user=self.tenant2)
        payload['department'] = self.dept_t2.id
        payload['designation'] = self.desig_t2.id
        payload['full_name'] = 'Charlie'
        
        res3 = self.client.post(self.list_url, payload)
        self.assertEqual(res3.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res3.data['employee_code'], 'EMP-0001')

    def test_cross_tenant_user_fk_validation(self):
        """Test assigning a cross-tenant user returns HTTP 400."""
        self.client.force_authenticate(user=self.tenant1)
        
        payload = {
            'full_name': 'Alice',
            'date_of_birth': '1990-01-01',
            'date_of_joining': '2023-01-01',
            'gender': 'F',
            'employment_type': 'full_time',
            'department': self.dept_t1.id,
            'designation': self.desig_t1.id,
            'work_state': 'Maharashtra',
            'personal_phone': '9876543210',
            'bank_name': 'HDFC Bank',
            'bank_account_number': '1234567890',
            'bank_ifsc': 'HDFC0001234',
            'account_holder_name': 'Alice',
            'user': self.t2_user.id  # Trying to link T2's user to T1's employee
        }
        
        res = self.client.post(self.list_url, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('user', res.data)
        self.assertIn('does not belong to the active tenant', str(res.data['user']))

    def test_inactive_employee_exclusion_from_active_list(self):
        """Test inactive employee is excluded when ?status=active is passed."""
        # Create an Active employee
        Employee.objects.create(
            tenant=self.tenant1,
            full_name='Active Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept_t1,
            designation=self.desig_t1,
            work_state='Maharashtra',
            status='active'
        )
        # Create an Inactive employee
        Employee.objects.create(
            tenant=self.tenant1,
            full_name='Inactive Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='F',
            employment_type='full_time',
            department=self.dept_t1,
            designation=self.desig_t1,
            work_state='Maharashtra',
            status='inactive'
        )

        self.client.force_authenticate(user=self.tenant1)
        
        # Test without filter (should return both)
        res_all = self.client.get(self.list_url)
        self.assertEqual(res_all.status_code, status.HTTP_200_OK)
        self.assertEqual(res_all.data['count'], 2)
        
        # Test with ?status=active filter
        res_active = self.client.get(self.list_url + '?status=active')
        self.assertEqual(res_active.status_code, status.HTTP_200_OK)
        self.assertEqual(res_active.data['count'], 1)
        self.assertEqual(res_active.data['results'][0]['full_name'], 'Active Emp')

    def test_cross_tenant_isolation(self):
        """Test cross-tenant isolation returns HTTP 404."""
        emp_t1 = Employee.objects.create(
            tenant=self.tenant1,
            full_name='T1 Emp',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept_t1,
            designation=self.desig_t1,
            work_state='Maharashtra',
            status='active'
        )
        
        self.client.force_authenticate(user=self.tenant2)
        detail_url = reverse('employee-detail', args=[emp_t1.id])
        
        # Get
        res_get = self.client.get(detail_url)
        self.assertEqual(res_get.status_code, status.HTTP_404_NOT_FOUND)
        
        # Update
        res_put = self.client.put(detail_url, {'full_name': 'Hacked'})
        self.assertEqual(res_put.status_code, status.HTTP_404_NOT_FOUND)
        
        # Delete
        res_delete = self.client.delete(detail_url)
        self.assertEqual(res_delete.status_code, status.HTTP_404_NOT_FOUND)

    def test_employee_validations_dob_phone_bank(self):
        """Test mandatory phone, age >= 13, and bank details."""
        self.client.force_authenticate(user=self.tenant1)

        base_payload = {
            'full_name': 'Validation Tester',
            'date_of_birth': '2000-01-01',
            'date_of_joining': '2023-01-01',
            'gender': 'M',
            'employment_type': 'full_time',
            'department': self.dept_t1.id,
            'designation': self.desig_t1.id,
            'work_state': 'Maharashtra',
            'personal_phone': '9876543210',
            'bank_name': 'HDFC Bank',
            'bank_account_number': '1234567890',
            'bank_ifsc': 'HDFC0001234',
            'account_holder_name': 'Validation Tester',
        }

        # 1. Missing phone
        p1 = dict(base_payload)
        del p1['personal_phone']
        r1 = self.client.post(self.list_url, p1)
        self.assertEqual(r1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('personal_phone', r1.data)

        # 2. DOB under 13 years
        p2 = dict(base_payload)
        p2['date_of_birth'] = (datetime.date.today() - datetime.timedelta(days=365*10)).isoformat()
        r2 = self.client.post(self.list_url, p2)
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date_of_birth', r2.data)

        # 3. Missing bank details
        p3 = dict(base_payload)
        del p3['bank_account_number']
        r3 = self.client.post(self.list_url, p3)
        self.assertEqual(r3.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('bank_account_number', r3.data)

    def test_delete_employee_with_payslip_cascades_cleanly(self):
        """Test deleting employee with payslip does not return 500 and cascades."""
        from decimal import Decimal
        from hr.models import PayrollRun, Payslip
        self.client.force_authenticate(user=self.tenant1)

        emp = Employee.objects.create(
            tenant=self.tenant1,
            full_name='Deletable Emp',
            date_of_birth=datetime.date(1995, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept_t1,
            designation=self.desig_t1,
            work_state='Maharashtra',
            personal_phone='9876543210',
            bank_name='HDFC',
            bank_account_number='123456',
            bank_ifsc='HDFC0001234',
            account_holder_name='Deletable Emp'
        )

        pr = PayrollRun.objects.create(
            tenant=self.tenant1, month=2, year=2025, status='draft',
            total_gross=Decimal('0'), total_net=Decimal('0')
        )
        ps = Payslip.objects.create(
            tenant=self.tenant1, payroll_run=pr, employee=emp,
            present_days=20, total_working_days=20,
            gross_salary=Decimal('50000.00'), net_salary=Decimal('45000.00')
        )

        detail_url = reverse('employee-detail', args=[emp.id])
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Employee.objects.filter(id=emp.id).exists())
        self.assertFalse(Payslip.objects.filter(id=ps.id).exists())

    def test_update_employee_salary_updates_latest_assignment(self):
        """Test editing an existing employee's salary properly updates the latest assignment and salary details."""
        from decimal import Decimal
        self.client.force_authenticate(user=self.tenant1)

        # 1. Create employee with initial salary of 50k and joining date in 2024
        create_payload = {
            'full_name': 'Salary Update Test',
            'date_of_birth': '1992-05-15',
            'date_of_joining': '2024-01-01',
            'gender': 'M',
            'employment_type': 'full_time',
            'department': str(self.dept_t1.id),
            'designation': str(self.desig_t1.id),
            'work_state': 'Karnataka',
            'personal_phone': '9876543210',
            'bank_name': 'HDFC',
            'bank_account_number': '987654321',
            'bank_ifsc': 'HDFC0001234',
            'account_holder_name': 'Salary Update Test',
            'salary': {
                'monthly_ctc': 50000,
                'components': {'Basic': 25000, 'HRA': 12500, 'Special Allowance': 12500}
            }
        }
        res = self.client.post(self.list_url, create_payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        emp_id = res.data['id']
        self.assertEqual(res.data['current_ctc'], '50000.00')

        # 2. Update employee salary to 100k (with or without backdated effective_from)
        update_payload = {
            'salary': {
                'monthly_ctc': 100000,
                'components': {'Basic': 50000, 'HRA': 25000, 'Special Allowance': 25000}
            }
        }
        detail_url = reverse('employee-detail', args=[emp_id])
        res_update = self.client.patch(detail_url, update_payload, format='json')
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        self.assertEqual(res_update.data['current_ctc'], '100000.00')
        self.assertIsNotNone(res_update.data.get('salary_details'))
        self.assertEqual(res_update.data['salary_details']['monthly_ctc'], '100000.00')
        self.assertIn('monthly_net_take_home', res_update.data['salary_details'])
        self.assertIn('earnings', res_update.data['salary_details'])
        self.assertIn('employee_deductions', res_update.data['salary_details'])
        self.assertIn('tds_details', res_update.data['salary_details'])

        # 3. Update again passing effective_from as date_of_joining (the previous bug condition)
        update_payload_backdated = {
            'salary': {
                'monthly_ctc': 120000,
                'effective_from': '2024-01-01',
                'components': {'Basic': 60000, 'HRA': 30000, 'Special Allowance': 30000}
            }
        }
        res_update2 = self.client.patch(detail_url, update_payload_backdated, format='json')
        self.assertEqual(res_update2.status_code, status.HTTP_200_OK)
        self.assertEqual(res_update2.data['current_ctc'], '120000.00')
        self.assertEqual(res_update2.data['salary_details']['monthly_ctc'], '120000.00')

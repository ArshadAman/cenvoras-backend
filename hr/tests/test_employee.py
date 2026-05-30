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
            'work_state': 'Maharashtra'
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

"""
Unit tests for Department and Designation ViewSets.

Requirements: 3.1, 3.2, 3.3, 3.4, 12.2
- Test unique-name constraint per tenant
- Test deletion blocked when active employees exist
- Test cross-tenant isolation (HTTP 404 for foreign tenant IDs)
"""

import datetime
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee


class DepartmentDesignationAPITests(APITestCase):

    def setUp(self):
        # Create Tenant 1 (and an admin user for it)
        self.tenant1 = User.objects.create_user(
            username='tenant1_owner',
            email='tenant1@example.com',
            password='testpass',
            business_name='Tenant 1 Corp',
            role='admin'
        )
        # Create Tenant 2 (and an admin user for it)
        self.tenant2 = User.objects.create_user(
            username='tenant2_owner',
            email='tenant2@example.com',
            password='testpass',
            business_name='Tenant 2 Corp',
            role='admin'
        )

        # Base URLs
        self.dept_list_url = reverse('department-list')
        self.desig_list_url = reverse('designation-list')

    def test_create_department_and_designation(self):
        """Test successful creation sets tenant and returns 201."""
        self.client.force_authenticate(user=self.tenant1)
        
        # Create Department
        res_dept = self.client.post(self.dept_list_url, {'name': 'Engineering'})
        self.assertEqual(res_dept.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Department.objects.filter(tenant=self.tenant1, name='Engineering').count(), 1)
        
        # Create Designation
        res_desig = self.client.post(self.desig_list_url, {'name': 'Software Engineer'})
        self.assertEqual(res_desig.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Designation.objects.filter(tenant=self.tenant1, name='Software Engineer').count(), 1)

    def test_unique_name_constraint_per_tenant(self):
        """Test unique-name constraint per tenant."""
        # Create existing dept/desig in tenant1
        Department.objects.create(tenant=self.tenant1, name='HR')
        Designation.objects.create(tenant=self.tenant1, name='Manager')

        self.client.force_authenticate(user=self.tenant1)

        # Duplicate in tenant1 should fail
        res_dept = self.client.post(self.dept_list_url, {'name': 'HR'})
        self.assertEqual(res_dept.status_code, status.HTTP_400_BAD_REQUEST)
        
        res_desig = self.client.post(self.desig_list_url, {'name': 'Manager'})
        self.assertEqual(res_desig.status_code, status.HTTP_400_BAD_REQUEST)

        # Same names in tenant2 should succeed
        self.client.force_authenticate(user=self.tenant2)
        res_dept2 = self.client.post(self.dept_list_url, {'name': 'HR'})
        self.assertEqual(res_dept2.status_code, status.HTTP_201_CREATED)
        
        res_desig2 = self.client.post(self.desig_list_url, {'name': 'Manager'})
        self.assertEqual(res_desig2.status_code, status.HTTP_201_CREATED)

    def test_deletion_blocked_when_active_employees_exist(self):
        """Test deletion blocked when active employees exist (HTTP 400)."""
        dept = Department.objects.create(tenant=self.tenant1, name='Sales')
        desig = Designation.objects.create(tenant=self.tenant1, name='Sales Exec')
        
        # Create an active employee
        Employee.objects.create(
            tenant=self.tenant1,
            full_name='John Doe',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='M',
            employment_type='full_time',
            department=dept,
            designation=desig,
            work_state='Maharashtra',
            status='active'
        )

        self.client.force_authenticate(user=self.tenant1)
        
        # Attempt to delete Department
        dept_url = reverse('department-detail', args=[dept.id])
        res_dept = self.client.delete(dept_url)
        self.assertEqual(res_dept.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot delete department", str(res_dept.data))
        
        # Attempt to delete Designation
        desig_url = reverse('designation-detail', args=[desig.id])
        res_desig = self.client.delete(desig_url)
        self.assertEqual(res_desig.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot delete designation", str(res_desig.data))
        
        # Verify they still exist
        self.assertTrue(Department.objects.filter(id=dept.id).exists())
        self.assertTrue(Designation.objects.filter(id=desig.id).exists())

    def test_deletion_allowed_when_no_active_employees(self):
        """Test deletion works when no active employees are assigned."""
        dept = Department.objects.create(tenant=self.tenant1, name='Support')
        desig = Designation.objects.create(tenant=self.tenant1, name='Support Agent')
        
        # Create an INACTIVE employee
        Employee.objects.create(
            tenant=self.tenant1,
            full_name='Jane Doe',
            date_of_birth=datetime.date(1995, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='F',
            employment_type='full_time',
            department=dept,
            designation=desig,
            work_state='Maharashtra',
            status='inactive'
        )

        self.client.force_authenticate(user=self.tenant1)
        
        dept_url = reverse('department-detail', args=[dept.id])
        res_dept = self.client.delete(dept_url)
        self.assertEqual(res_dept.status_code, status.HTTP_204_NO_CONTENT)
        
        desig_url = reverse('designation-detail', args=[desig.id])
        res_desig = self.client.delete(desig_url)
        self.assertEqual(res_desig.status_code, status.HTTP_204_NO_CONTENT)

    def test_cross_tenant_isolation(self):
        """Test cross-tenant isolation (HTTP 404 for foreign tenant IDs)."""
        # Create dept/desig in tenant 1
        dept_t1 = Department.objects.create(tenant=self.tenant1, name='IT')
        desig_t1 = Designation.objects.create(tenant=self.tenant1, name='IT Admin')
        
        # Authenticate as tenant 2
        self.client.force_authenticate(user=self.tenant2)
        
        # Attempt to read tenant 1's department
        dept_url = reverse('department-detail', args=[dept_t1.id])
        res_dept = self.client.get(dept_url)
        self.assertEqual(res_dept.status_code, status.HTTP_404_NOT_FOUND)
        
        # Attempt to update tenant 1's designation
        desig_url = reverse('designation-detail', args=[desig_t1.id])
        res_desig = self.client.put(desig_url, {'name': 'Hacked Admin'})
        self.assertEqual(res_desig.status_code, status.HTTP_404_NOT_FOUND)
        
        # Attempt to delete tenant 1's department
        res_delete = self.client.delete(dept_url)
        self.assertEqual(res_delete.status_code, status.HTTP_404_NOT_FOUND)

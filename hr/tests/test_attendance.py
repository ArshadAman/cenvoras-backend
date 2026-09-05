"""
Unit tests for Attendance Tracking API.

Requirements: 4.2, 4.3, 4.4
- Test duplicate (employee, date) results in update, not duplicate record
- Test bulk endpoint processes all records in one transaction
"""

import datetime
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee, AttendanceRecord


class AttendanceAPITests(APITestCase):

    def setUp(self):
        # Create Tenant
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='testpass',
            business_name='Tenant Corp',
            role='admin'
        )

        # Department & Designation
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')

        # Create Employees
        self.emp1 = Employee.objects.create(
            tenant=self.tenant,
            full_name='Alice',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='F',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Maharashtra',
            status='active'
        )
        self.emp2 = Employee.objects.create(
            tenant=self.tenant,
            full_name='Bob',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Maharashtra',
            status='active'
        )

        self.list_url = reverse('attendance-list')
        self.bulk_url = reverse('bulk-attendance')

    def test_upsert_logic_prevents_duplicates(self):
        """Test duplicate (employee, date) results in update, not duplicate record."""
        self.client.force_authenticate(user=self.tenant)
        
        # Initial creation
        payload = {
            'employee': self.emp1.id,
            'date': '2023-10-01',
            'status': 'present'
        }
        res_create = self.client.post(self.list_url, payload)
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.emp1, date='2023-10-01').count(), 1)
        
        # Upsert (update existing)
        payload['status'] = 'absent'
        res_update = self.client.post(self.list_url, payload)
        if res_update.status_code != 200:
            print(res_update.data)
        self.assertEqual(res_update.status_code, status.HTTP_200_OK)
        
        # Verify no duplicate was created and status is updated
        records = AttendanceRecord.objects.filter(employee=self.emp1, date='2023-10-01')
        self.assertEqual(records.count(), 1)
        self.assertEqual(records.first().status, 'absent')

    def test_bulk_endpoint_transaction(self):
        """Test bulk endpoint processes all records in one transaction."""
        self.client.force_authenticate(user=self.tenant)
        
        # Prepare valid data
        payload = [
            {'employee_id': str(self.emp1.id), 'date': '2023-10-02', 'status': 'present'},
            {'employee_id': str(self.emp2.id), 'date': '2023-10-02', 'status': 'half_day'}
        ]
        
        res = self.client.post(self.bulk_url, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['created'], 2)
        
        # Verify records created
        self.assertEqual(AttendanceRecord.objects.filter(date='2023-10-02').count(), 2)
        
        # Upsert one, Create one
        payload2 = [
            {'employee_id': str(self.emp1.id), 'date': '2023-10-02', 'status': 'absent'}, # Update
            {'employee_id': str(self.emp1.id), 'date': '2023-10-03', 'status': 'present'}, # Create
        ]
        res2 = self.client.post(self.bulk_url, payload2, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data['created'], 1)
        self.assertEqual(res2.data['updated'], 1)
        
        # Check update applied
        rec = AttendanceRecord.objects.get(employee=self.emp1, date='2023-10-02')
        self.assertEqual(rec.status, 'absent')

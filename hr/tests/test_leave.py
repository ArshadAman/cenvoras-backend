"""
Unit tests for Leave Service and Leave Application lifecycle.

Requirements: 5.3, 5.4, 6.2, 6.4, 6.5
- Test compute_leave_days excludes Sundays and Holiday attendance records
- Test approval decrements balance and creates attendance records
- Test excess days beyond balance are marked as LWP
- Test rejection leaves attendance unchanged
"""

import datetime
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee, AttendanceRecord, LeaveType, LeaveBalance, LeaveApplication
from hr.services.leave_service import compute_leave_days, get_or_init_leave_balance


class LeaveServiceTests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='testpass',
            business_name='Tenant Corp',
            role='admin'
        )
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')
        self.emp = Employee.objects.create(
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

    def test_compute_leave_days_excludes_sundays_and_holidays(self):
        """Test compute_leave_days excludes Sundays and Holiday attendance records."""
        # 2023-10-01 is a Sunday. 2023-10-02 to 2023-10-07 are Mon-Sat.
        start_date = datetime.date(2023, 10, 1)
        end_date = datetime.date(2023, 10, 7)
        
        # 7 days total. Sunday is excluded -> 6 days.
        # Let's add a holiday on Wednesday, 2023-10-04.
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            date=datetime.date(2023, 10, 4),
            status='holiday'
        )
        
        days = compute_leave_days(start_date, end_date, self.emp)
        self.assertEqual(days, Decimal('5.0'))


class LeaveLifecycleAPITests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='testpass',
            business_name='Tenant Corp',
            role='admin'
        )
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')
        self.emp = Employee.objects.create(
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
        
        self.leave_type = LeaveType.objects.create(
            tenant=self.tenant,
            name='Annual Leave',
            annual_entitlement=Decimal('10.0')
        )

    def test_approval_decrements_balance_and_creates_attendance(self):
        """Test approval decrements balance and creates attendance records."""
        # Create LeaveApplication
        start_date = datetime.date(2023, 10, 2) # Monday
        end_date = datetime.date(2023, 10, 3) # Tuesday
        app = LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            leave_type=self.leave_type,
            start_date=start_date,
            end_date=end_date,
            reason='Vacation',
            status='pending',
            computed_days=Decimal('2.0')
        )
        
        self.client.force_authenticate(user=self.tenant)
        url = reverse('leave-approve', args=[app.id])
        res = self.client.post(url)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        
        # Verify application status
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')
        self.assertEqual(app.lwp_days, Decimal('0.0'))
        
        # Verify balance decremented
        balance = LeaveBalance.objects.get(employee=self.emp, leave_type=self.leave_type, year=2023)
        self.assertEqual(balance.balance, Decimal('8.0'))
        
        # Verify attendance records created
        att_mon = AttendanceRecord.objects.get(employee=self.emp, date=start_date)
        self.assertEqual(att_mon.status, 'leave')
        att_tue = AttendanceRecord.objects.get(employee=self.emp, date=end_date)
        self.assertEqual(att_tue.status, 'leave')

    def test_excess_days_marked_as_lwp(self):
        """Test excess days beyond balance are marked as LWP."""
        # Provide only 2 days of balance initially
        LeaveBalance.objects.create(
            employee=self.emp,
            leave_type=self.leave_type,
            year=2023,
            balance=Decimal('2.0')
        )
        
        start_date = datetime.date(2023, 10, 2)
        end_date = datetime.date(2023, 10, 6) # 5 days
        app = LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            leave_type=self.leave_type,
            start_date=start_date,
            end_date=end_date,
            reason='Long Vacation',
            status='pending',
            computed_days=Decimal('5.0')
        )
        
        self.client.force_authenticate(user=self.tenant)
        url = reverse('leave-approve', args=[app.id])
        res = self.client.post(url)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')
        self.assertEqual(app.lwp_days, Decimal('3.0')) # 5 computed - 2 balance = 3 LWP
        
        balance = LeaveBalance.objects.get(employee=self.emp, leave_type=self.leave_type, year=2023)
        self.assertEqual(balance.balance, Decimal('0.0'))

    def test_rejection_leaves_attendance_unchanged(self):
        """Test rejection sets status to rejected and leaves attendance unchanged."""
        start_date = datetime.date(2023, 10, 2)
        end_date = datetime.date(2023, 10, 3)
        app = LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            leave_type=self.leave_type,
            start_date=start_date,
            end_date=end_date,
            reason='Vacation',
            status='pending',
            computed_days=Decimal('2.0')
        )
        
        self.client.force_authenticate(user=self.tenant)
        url = reverse('leave-reject', args=[app.id])
        res = self.client.post(url)
        
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        
        app.refresh_from_db()
        self.assertEqual(app.status, 'rejected')
        
        # Verify no balance created or altered
        self.assertFalse(LeaveBalance.objects.filter(employee=self.emp, leave_type=self.leave_type, year=2023).exists())
        
        # Verify no attendance created
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.emp, date__range=(start_date, end_date)).exists())

import datetime
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee, AttendanceRecord, LeaveType, LeaveBalance, LeaveApplication
from hr.serializers import LeaveBalanceSerializer


class CheckInAndLeaveBalancesTests(APITestCase):

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

        self.emp_user = User.objects.create_user(
            username='emp_priya',
            email='priya@example.com',
            password='testpass',
            role='employee'
        )

        self.employee = Employee.objects.create(
            tenant=self.tenant,
            user=self.emp_user,
            full_name='Priya Sharma',
            personal_email='priya@example.com',
            date_of_birth=datetime.date(1995, 5, 10),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='F',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Maharashtra',
            status='active'
        )

    def test_employee_check_in_and_check_out_flow(self):
        self.client.force_authenticate(user=self.emp_user)

        # 1. Initial today-status: not checked in
        today_status_url = reverse('attendance-today-status')
        res = self.client.get(today_status_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['checked_in'])
        self.assertFalse(res.data['checked_out'])
        self.assertIsNone(res.data['check_in_time'])
        self.assertIsNone(res.data['check_out_time'])

        # 2. Check In
        check_in_url = reverse('attendance-check-in')
        res_in = self.client.post(check_in_url, {})
        self.assertEqual(res_in.status_code, status.HTTP_200_OK)
        self.assertEqual(res_in.data['status'], 'checked_in')
        self.assertTrue(res_in.data['checked_in'])
        self.assertFalse(res_in.data['checked_out'])
        self.assertIsNotNone(res_in.data['check_in_time'])

        # Verify DB
        today = timezone.now().date()
        att_rec = AttendanceRecord.objects.get(employee=self.employee, date=today)
        self.assertEqual(att_rec.status, 'present')
        self.assertIsNotNone(att_rec.check_in_time)
        self.assertIsNone(att_rec.check_out_time)

        # 3. Today-status after check-in
        res_status = self.client.get(today_status_url)
        self.assertEqual(res_status.status_code, status.HTTP_200_OK)
        self.assertTrue(res_status.data['checked_in'])
        self.assertFalse(res_status.data['checked_out'])
        self.assertEqual(res_status.data['check_in_time'], res_in.data['check_in_time'])

        # 4. Check Out
        check_out_url = reverse('attendance-check-out')
        res_out = self.client.post(check_out_url, {})
        self.assertEqual(res_out.status_code, status.HTTP_200_OK)
        self.assertEqual(res_out.data['status'], 'checked_out')
        self.assertTrue(res_out.data['checked_in'])
        self.assertTrue(res_out.data['checked_out'])
        self.assertIsNotNone(res_out.data['check_in_time'])
        self.assertIsNotNone(res_out.data['check_out_time'])

        # Verify DB has check_out_time
        att_rec.refresh_from_db()
        self.assertIsNotNone(att_rec.check_out_time)

        # 5. Today-status after check-out
        res_status2 = self.client.get(today_status_url)
        self.assertEqual(res_status2.status_code, status.HTTP_200_OK)
        self.assertTrue(res_status2.data['checked_in'])
        self.assertTrue(res_status2.data['checked_out'])
        self.assertEqual(res_status2.data['check_out_time'], res_out.data['check_out_time'])

    def test_leave_balance_serializer_allocated_and_used_calculation(self):
        # Create LeaveType (Casual Leave, 12 days)
        casual_leave = LeaveType.objects.create(
            tenant=self.tenant,
            name='Casual Leave',
            annual_entitlement=Decimal('12.0'),
            is_paid=True
        )

        # Create initial balance
        balance_obj = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=casual_leave,
            year=2026,
            balance=Decimal('12.0')
        )

        # Initial serialization: allocated = 12, used = 0
        ser_data = LeaveBalanceSerializer(balance_obj).data
        self.assertEqual(ser_data['allocated'], 12)
        self.assertEqual(ser_data['used'], 0)

        # Apply for 1-day Casual Leave on 20 Nov 2026 (a Friday)
        app = LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            leave_type=casual_leave,
            start_date=datetime.date(2026, 11, 20),
            end_date=datetime.date(2026, 11, 20),
            computed_days=Decimal('1.0'),
            reason='Personal work',
            status='approved'
        )
        balance_obj.balance = Decimal('11.0')
        balance_obj.save()

        # Serialization with approved leave: allocated = 12, used = 1
        ser_data2 = LeaveBalanceSerializer(balance_obj).data
        self.assertEqual(ser_data2['allocated'], 12)
        self.assertEqual(ser_data2['used'], 1)
        self.assertEqual(ser_data2['balance'], '11.0')

    def test_leave_balances_api_endpoint_for_employee(self):
        casual_leave = LeaveType.objects.create(
            tenant=self.tenant,
            name='Casual Leave',
            annual_entitlement=Decimal('12.0'),
            is_paid=True
        )
        balance_obj = LeaveBalance.objects.create(
            employee=self.employee,
            leave_type=casual_leave,
            year=2026,
            balance=Decimal('11.0')
        )
        LeaveApplication.objects.create(
            tenant=self.tenant,
            employee=self.employee,
            leave_type=casual_leave,
            start_date=datetime.date(2026, 11, 20),
            end_date=datetime.date(2026, 11, 20),
            computed_days=Decimal('1.0'),
            reason='Casual leave testing',
            status='approved'
        )

        self.client.force_authenticate(user=self.emp_user)
        list_url = reverse('leave-balance-list')
        res = self.client.get(list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get('results', res.data)
        cl_entry = next((item for item in results if item['leave_type_name'] == 'Casual Leave'), None)
        self.assertIsNotNone(cl_entry)
        self.assertEqual(cl_entry['allocated'], 12)
        self.assertEqual(cl_entry['used'], 1)

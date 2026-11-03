"""UAT P1 items 8 (PAN/IFSC/DOJ/TDS-PAN) and 9 (UAN/PF/ESI saved and validated)."""
import datetime
from decimal import Decimal

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from hr.models import Department, Designation, Employee, HRMSSettings
from hr.validators import esi_ip_error, ifsc_error, pan_error, pf_member_error, uan_error
from users.models import User


class ValidatorTests(SimpleTestCase):
    def test_pan(self):
        self.assertIsNone(pan_error(' abcpe1234f ', individual=True))
        self.assertIsNone(pan_error('AAACB1234C'))  # company PAN, fine for a non-employee
        self.assertIn("individual", pan_error('AAACB1234C', individual=True))
        self.assertIn('4th character', pan_error('ABCXE1234F'))
        for bad in ('ABCPE1234', 'ABCP1234FF', '12345ABCDE', 'ABCPE12345'):
            self.assertIsNotNone(pan_error(bad), bad)

    def test_ifsc(self):
        self.assertIsNone(ifsc_error('sbin0001234'))
        self.assertIsNone(ifsc_error('HDFC0ABC123'))
        for bad in ('SBIN1001234', 'SBI00001234', 'SBIN000123', 'SBIN00012345'):
            self.assertIsNotNone(ifsc_error(bad), bad)

    def test_uan_esi_pf(self):
        self.assertIsNone(uan_error('100123456789'))
        self.assertIsNotNone(uan_error('10012345678'))
        self.assertIsNotNone(uan_error('10012345678A'))
        self.assertIsNone(esi_ip_error('3112345678'))
        self.assertIsNotNone(esi_ip_error('311234567'))
        self.assertIsNone(pf_member_error('mhban00123450000012345'))
        self.assertIsNotNone(pf_member_error('MHBAN0012345000001234'))  # 21 chars
        self.assertIsNotNone(pf_member_error('MHBA100123450000012345'))


class EmployeeIdsApiTests(APITestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(username='uat_hr', email='uat_hr@example.com', password='x',
                                               business_name='UAT Sharma Traders', role='admin')
        self.client.force_authenticate(user=self.tenant)
        self.dept = Department.objects.create(tenant=self.tenant, name='Ops')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Clerk')
        self.url = reverse('employee-list')

    def _payload(self, **kw):
        p = {'full_name': 'Asha', 'date_of_birth': '1995-01-01', 'date_of_joining': '2026-08-01', 'gender': 'F',
             'employment_type': 'full_time', 'department': self.dept.id, 'designation': self.desig.id,
             'work_state': 'Odisha', 'personal_phone': '9876543210', 'bank_name': 'SBI',
             'bank_account_number': '1234567890', 'bank_ifsc': 'sbin0001234', 'account_holder_name': 'Asha'}
        p.update(kw)
        return p

    def test_form_aliases_are_saved_and_returned(self):
        res = self.client.post(self.url, self._payload(uan_number='100123456789', esi_number='3112345678',
                                                       pf_number='ODBBS00123450000012345',
                                                       pan_number='abcpe1234f'), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        emp = Employee.objects.get(pk=res.data['id'])
        self.assertEqual((emp.uan, emp.esi_ip_number, emp.pf_member_id, emp.pan_number, emp.bank_ifsc),
                         ('100123456789', '3112345678', 'ODBBS00123450000012345', 'ABCPE1234F', 'SBIN0001234'))
        self.assertEqual(res.data['uan_number'], '100123456789')
        self.assertEqual(res.data['esi_number'], '3112345678')

    def test_invalid_values_rejected(self):
        for kw, field in ((dict(pan_number='AAACB1234C'), 'pan_number'), (dict(bank_ifsc='SBIN1001234'), 'bank_ifsc'),
                          (dict(uan_number='123'), 'uan'), (dict(esi_number='12'), 'esi_ip_number'),
                          (dict(pf_number='X'), 'pf_member_id')):
            res = self.client.post(self.url, self._payload(**kw), format='json')
            self.assertEqual(res.status_code, 400, kw)
            self.assertIn(field, str(res.data), kw)

    def test_uan_required_for_pf_member_and_ip_for_esi(self):
        res = self.client.post(self.url, self._payload(pf_member=True), format='json')
        self.assertIn('UAN is mandatory', str(res.data))
        res = self.client.post(self.url, self._payload(esi_covered=True), format='json')
        self.assertIn('ESI IP number is mandatory', str(res.data))
        HRMSSettings.objects.create(tenant=self.tenant, pf_enabled=False, esi_enabled=False)
        self.assertEqual(self.client.post(self.url, self._payload(pf_member=True, esi_covered=True),
                                          format='json').status_code, 201)

    def test_future_doj_upcoming_and_company_start(self):
        future = (datetime.date.today() + datetime.timedelta(days=40)).isoformat()
        res = self.client.post(self.url, self._payload(date_of_joining=future), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['is_upcoming'])
        self.assertEqual(res.data['status_display'], 'Upcoming')
        HRMSSettings.objects.create(tenant=self.tenant, company_start_date=datetime.date(2026, 4, 1))
        res = self.client.post(self.url, self._payload(date_of_joining='2026-03-31'), format='json')
        self.assertIn('company start date', str(res.data))


class PanRequiredForTdsTests(APITestCase):
    def test_critical_exception_when_tds_and_no_pan(self):
        from hr.models import PayrollRun, Payslip, PayrollException
        from hr.services.exceptions_service import scan_payroll_exceptions
        tenant = User.objects.create_user(username='t_tds', email='t_tds@example.com', password='x', role='admin')
        dept = Department.objects.create(tenant=tenant, name='Ops')
        desig = Designation.objects.create(tenant=tenant, name='Mgr')
        emp = Employee.objects.create(tenant=tenant, full_name='No Pan', date_of_birth=datetime.date(1990, 1, 1),
                                      date_of_joining=datetime.date(2026, 8, 1), gender='M',
                                      employment_type='full_time', department=dept, designation=desig,
                                      work_state='Odisha', bank_account_number='1', bank_ifsc='SBIN0001234')
        run = PayrollRun.objects.create(tenant=tenant, month=10, year=2026)
        Payslip.objects.create(tenant=tenant, payroll_run=run, employee=emp, tds=Decimal('5000.00'),
                               present_days=Decimal('26'), total_working_days=26, gross_salary=Decimal('100000'),
                               earnings={}, deductions={}, deduction_reasons={}, net_salary=Decimal('95000'))
        scan_payroll_exceptions(run.id)
        self.assertTrue(PayrollException.objects.filter(payroll_run=run, employee=emp, severity='critical',
                                                        code='PAN_REQUIRED_FOR_TDS').exists())
        emp.pan_number = 'ABCPE1234F'
        emp.save()
        scan_payroll_exceptions(run.id)
        self.assertFalse(PayrollException.objects.filter(payroll_run=run, code='PAN_REQUIRED_FOR_TDS').exists())

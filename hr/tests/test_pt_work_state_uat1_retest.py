"""UAT item 1 retest: work state round trip, company-state default and PT fallback."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from rest_framework.test import APITestCase

from hr.models import Department, Designation, Employee
from hr.services.payroll_engine import compute_pt
from users.models import User

D = Decimal

BASE = {
    'full_name': 'QA Mid', 'personal_phone': '9999999999', 'date_of_birth': '1990-01-01',
    'date_of_joining': '2025-01-01', 'gender': 'M', 'employment_type': 'full_time',
    'bank_name': 'SBI', 'bank_account_number': '1234567890', 'bank_ifsc': 'SBIN0000001',
    'account_holder_name': 'QA Mid',
}


class _Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs', verbosity=0)

    def setUp(self):
        self.owner = User.objects.create_user(
            username='odisha_owner', email='o@example.com', password='x',
            business_name='Odisha Co', role='admin', state='21')
        self.client.force_authenticate(self.owner)
        self.dept = Department.objects.create(tenant=self.owner, name='Ops')
        self.desig = Designation.objects.create(tenant=self.owner, name='Clerk')

    def _create(self, **extra):
        res = self.client.post('/api/hr/employees/', {**BASE, 'department': str(self.dept.id), 'designation': str(self.desig.id), **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data


class WorkStateUAT1RetestTests(_Base):
    def test_new_employee_defaults_to_company_state(self):
        data = self._create()
        self.assertEqual(data['work_state'], 'Odisha')
        self.assertEqual(Employee.objects.get(pk=data['id']).work_state, 'Odisha')

    def test_work_state_round_trip_via_api(self):
        emp = self._create(work_state='Karnataka')
        self.assertEqual(emp['work_state'], 'Karnataka')
        res = self.client.patch(f"/api/hr/employees/{emp['id']}/", {'work_state': 'Odisha'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        got = self.client.get(f"/api/hr/employees/{emp['id']}/").data
        self.assertEqual(got['work_state'], 'Odisha')
        self.assertEqual(Employee.objects.get(pk=emp['id']).work_state, 'Odisha')
        # And back again
        self.client.patch(f"/api/hr/employees/{emp['id']}/", {'work_state': 'KA'}, format='json')
        self.assertEqual(self.client.get(f"/api/hr/employees/{emp['id']}/").data['work_state'], 'Karnataka')

    def test_empty_work_state_uses_company_state_for_pt(self):
        emp = Employee.objects.get(pk=self._create()['id'])
        Employee.objects.filter(pk=emp.pk).update(work_state='')
        emp.refresh_from_db()
        on = datetime.date(2026, 11, 28)
        # Odisha company → ₹0 (not Karnataka's ₹200)
        self.assertEqual(compute_pt(D('30000'), '', month=11, on_date=on, employee=emp, tenant=self.owner), D('0.00'))
        self.owner.state = '29'
        self.assertEqual(compute_pt(D('30000'), '', month=11, on_date=on, employee=emp, tenant=self.owner), D('200.00'))

    def test_odisha_zero_from_apr_2026_and_karnataka_200(self):
        for m in (4, 11):
            self.assertEqual(compute_pt(D('50000'), 'Odisha', month=m, on_date=datetime.date(2026, m, 28)), D('0.00'))
        self.assertEqual(compute_pt(D('25000'), 'Karnataka', month=11, on_date=datetime.date(2026, 11, 28)), D('200.00'))
        self.assertEqual(compute_pt(D('24999.99'), 'Karnataka', month=11, on_date=datetime.date(2026, 11, 28)), D('0.00'))


class WorkStateMismatchReportTests(_Base):
    def test_report_is_read_only_and_lists_mismatch(self):
        import io
        ka = Employee.objects.get(pk=self._create(work_state='Karnataka', full_name='KA Emp')['id'])
        self._create(full_name='OD Emp')
        out = io.StringIO()
        call_command('report_work_state_mismatch', stdout=out)
        text = out.getvalue()
        self.assertIn('KA Emp', text)
        self.assertNotIn('OD Emp', text)
        self.assertIn('1 employee(s) differ', text)
        ka.refresh_from_db()
        self.assertEqual(ka.work_state, 'Karnataka')


class ClosedRunRecalcBlockedTests(_Base):
    def test_approved_run_cannot_be_recalculated(self):
        from hr.models import PayrollRun
        for st in ('approved', 'paid', 'locked'):
            run = PayrollRun.objects.create(tenant=self.owner, month=2, year=2026, status=st)
            for action in ('calculate', 'run'):
                res = self.client.post(f'/api/hr/payroll-runs/{run.id}/{action}/')
                self.assertEqual(res.status_code, 400, (st, action, getattr(res, 'data', res)))
            run.refresh_from_db()
            self.assertEqual(run.status, st)
            run.delete()

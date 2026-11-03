"""Rudra CA conditions on payroll reopen (#220 follow-up):
- admin-only, mandatory reason, logged (who / when / why);
- only an approved-but-UNPAID run may be reopened and recalculated;
- a paid run, or one whose TDS/PF/ESI is deposited or filed, can't be reopened and its payslips
  are never regenerated: corrections go through arrears/recovery lines in the next open run.
Also: pre-April 2026 Odisha months use the old (pre-repeal, VERIFY) slabs, so PT is NOT ₹0."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from audit_log.models import AuditLog
from hr.models import PayrollRun
from hr.services.payroll_engine import compute_pt
from users.models import User


class PayrollReopenGuardTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='reopen_owner', email='ro@example.com', password='x',
            business_name='Reopen Co', role='admin')
        self.client.force_authenticate(self.owner)

    def _run(self, **kw):
        kw.setdefault('status', 'approved')
        kw.setdefault('approved_at', timezone.now())
        return PayrollRun.objects.create(tenant=self.owner, month=9, year=2026, **kw)

    def _reopen(self, run, reason='Leave correction', user=None):
        if user:
            self.client.force_authenticate(user)
        return self.client.post(f'/api/hr/payroll-runs/{run.id}/reopen/', {'reason': reason}, format='json')

    def test_non_admin_gets_403(self):
        run = self._run()
        for role in ('accountant', 'hr', 'manager'):
            u = User.objects.create_user(username=f'u_{role}', email=f'{role}@x.com', password='x',
                                         role=role, parent=self.owner)
            res = self._reopen(run, user=u)
            self.assertEqual(res.status_code, 403, (role, res.data))
        run.refresh_from_db()
        self.assertEqual(run.status, 'approved')

    def test_missing_reason_rejected(self):
        run = self._run()
        for reason in ('', '   '):
            res = self._reopen(run, reason=reason)
            self.assertEqual(res.status_code, 400)
            self.assertIn('reason', str(res.data).lower())
        res = self.client.post(f'/api/hr/payroll-runs/{run.id}/reopen/', {}, format='json')
        self.assertEqual(res.status_code, 400)
        run.refresh_from_db()
        self.assertEqual(run.status, 'approved')

    def test_unpaid_approved_run_reopens_logs_and_recalculates(self):
        run = self._run()
        res = self._reopen(run, reason='Missed overtime')
        self.assertEqual(res.status_code, 200, res.data)
        run.refresh_from_db()
        self.assertEqual(run.status, 'draft')
        self.assertEqual(run.reopened_by, self.owner)
        self.assertIsNotNone(run.reopened_at)
        self.assertEqual(run.reopen_reason, 'Missed overtime')
        self.assertEqual(run.reopen_history[-1]['reason'], 'Missed overtime')
        log = AuditLog.objects.filter(model_name='PayrollReopen', object_id=str(run.id)).latest('timestamp') \
            if hasattr(AuditLog, 'timestamp') else AuditLog.objects.filter(model_name='PayrollReopen', object_id=str(run.id)).last()
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.owner)
        self.assertIn('Missed overtime', str(log.changes))
        # Recalculation is now allowed
        res = self.client.post(f'/api/hr/payroll-runs/{run.id}/calculate/')
        self.assertEqual(res.status_code, 200, res.data)

    def test_paid_run_cannot_be_reopened(self):
        for kw in ({'status': 'paid', 'paid_at': timezone.now()},
                   {'status': 'locked', 'paid_at': timezone.now()}):
            run = self._run(**kw)
            res = self._reopen(run)
            self.assertEqual(res.status_code, 400, res.data)
            self.assertIn('arrears or recovery lines', str(res.data))
            run.refresh_from_db()
            self.assertEqual(run.status, kw['status'])
            self.assertIsNotNone(run.paid_at)
            self.assertFalse(run.is_reopened)
            run.delete()

    def test_tds_pf_esi_deposited_run_cannot_be_reopened(self):
        for field in ('tds_deposited_at', 'pf_deposited_at', 'esi_deposited_at'):
            run = self._run(**{field: timezone.now()})
            res = self._reopen(run)
            self.assertEqual(res.status_code, 400, (field, res.data))
            self.assertIn('arrears or recovery lines', str(res.data))
            run.refresh_from_db()
            self.assertEqual(run.status, 'approved')
            run.delete()

    def test_mark_tds_deposited_then_reopen_blocked(self):
        run = self._run()
        res = self.client.post(f'/api/hr/payroll-runs/{run.id}/mark-statutory-deposited/',
                               {'component': 'tds', 'deposited_at': '2026-10-07'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        run.refresh_from_db()
        self.assertIsNotNone(run.tds_deposited_at)
        self.assertEqual(self._reopen(run).status_code, 400)

    def test_paid_or_deposited_run_never_regenerated_even_if_reopened(self):
        # Simulate a run that somehow got to draft while paid / deposited.
        for kw in ({'paid_at': timezone.now()}, {'tds_deposited_at': timezone.now()}):
            run = self._run(status='draft', is_reopened=True, **kw)
            for action in ('calculate', 'run'):
                res = self.client.post(f'/api/hr/payroll-runs/{run.id}/{action}/')
                self.assertEqual(res.status_code, 400, (kw, action, res.data))
                self.assertIn('arrears or recovery lines', str(res.data))
            run.refresh_from_db()
            self.assertEqual(run.status, 'draft')
            run.delete()


class OdishaPreRepealPTTests(APITestCase):
    """Before 1 Apr 2026 Odisha PT was payable on the old schedule (rows marked VERIFY)."""
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs', verbosity=0)

    def test_pre_april_2026_odisha_uses_old_slabs_non_zero_at_15k(self):
        for m in (1, 2, 3):
            pt = compute_pt(Decimal('15000'), 'Odisha', month=m, on_date=datetime.date(2026, m, 28))
            self.assertGreater(pt, Decimal('0'), m)
            self.assertEqual(pt, Decimal('125.00'), m)
        # Feb 2026 is NOT ₹0 for Odisha
        self.assertNotEqual(compute_pt(Decimal('15000'), 'Odisha', month=2, on_date=datetime.date(2026, 2, 28)), Decimal('0.00'))
        # From April 2026 (repeal) → ₹0
        self.assertEqual(compute_pt(Decimal('15000'), 'Odisha', month=4, on_date=datetime.date(2026, 4, 28)), Decimal('0.00'))

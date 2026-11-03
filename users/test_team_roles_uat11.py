"""UAT P1 #11: least-access team role default, role-change audit, salary visibility."""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit_log.models import AuditLog
from hr.serializers import can_view_salary
from users.serializers import TeamMemberSerializer

User = get_user_model()


class TeamRoleUAT11Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='owner11@x.in', email='owner11@x.in', password='pw123456!', role='admin')
        self.req = SimpleNamespace(user=self.admin, META={'REMOTE_ADDR': '127.0.0.1'})

    def _create(self, **extra):
        data = {'email': 'm11@x.in', 'first_name': 'M', 'password': 'pw123456!', **extra}
        s = TeamMemberSerializer(data=data, context={'request': self.req})
        self.assertTrue(s.is_valid(), s.errors)
        return s.save()

    def test_default_role_is_employee_not_admin_or_manager(self):
        member = self._create()
        self.assertEqual(member.role, 'employee')

    def test_explicit_role_kept(self):
        self.assertEqual(self._create(role='accountant').role, 'accountant')

    def test_role_change_is_audited_with_old_and_new(self):
        member = self._create(role='employee')
        s = TeamMemberSerializer(member, data={'role': 'manager'}, partial=True,
                                 context={'request': self.req})
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        logs = [l for l in AuditLog.objects.filter(object_id=str(member.id))
                if (l.changes or {}).get('event') == 'role_change']
        changes = [(l.changes['old'], l.changes['new']) for l in logs]
        self.assertIn((None, 'employee'), changes)
        self.assertIn(('employee', 'manager'), changes)
        last = [l for l in logs if l.changes['new'] == 'manager'][0]
        self.assertEqual(last.user_id, self.admin.id)
        self.assertIsNotNone(last.timestamp)

    def test_role_change_writes_only_the_structured_row(self):
        member = self._create(role='employee')
        before = AuditLog.objects.filter(object_id=str(member.id)).count()
        s = TeamMemberSerializer(member, data={'role': 'manager'}, partial=True,
                                 context={'request': self.req})
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        new = list(AuditLog.objects.filter(object_id=str(member.id)).order_by('timestamp')[before:])
        role_rows = [l for l in new if 'role' in (l.changes or {}) or (l.changes or {}).get('event') == 'role_change']
        self.assertEqual(len(role_rows), 1, [l.changes for l in new])
        self.assertEqual(role_rows[0].changes['event'], 'role_change')

    def test_no_audit_when_role_unchanged(self):
        member = self._create(role='hr')
        s = TeamMemberSerializer(member, data={'first_name': 'Z'}, partial=True,
                                 context={'request': self.req})
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        n = sum(1 for l in AuditLog.objects.filter(object_id=str(member.id))
                if (l.changes or {}).get('event') == 'role_change')
        self.assertEqual(n, 1)


class SalaryVisibilityUAT11Tests(TestCase):
    def _u(self, role, uid=1):
        return SimpleNamespace(is_authenticated=True, role=role, id=uid)

    def test_hr_admin_accountant_see_salary(self):
        for r in ('admin', 'hr', 'accountant'):
            self.assertTrue(can_view_salary(self._u(r)))

    def test_manager_and_salesman_do_not(self):
        for r in ('manager', 'salesman'):
            self.assertFalse(can_view_salary(self._u(r), SimpleNamespace(user_id=1)))

    def test_employee_sees_only_own(self):
        self.assertTrue(can_view_salary(self._u('employee', 7), SimpleNamespace(user_id=7)))
        self.assertFalse(can_view_salary(self._u('employee', 7), SimpleNamespace(user_id=8)))

"""
Unit tests for HRPermission DRF permission class.

Tests cover all four roles (admin, manager, accountant, salesman) and edge cases
(unauthenticated user, unknown role) for both has_permission and has_object_permission.

Requirements: 11.1, 11.2, 11.3, 11.4
"""

from unittest.mock import MagicMock, patch
from django.test import TestCase

from hr.permissions import HRPermission


def make_request(method='GET', role='admin', authenticated=True):
    """Helper: build a mock DRF request with the given HTTP method and user role."""
    request = MagicMock()
    request.method = method
    if authenticated:
        request.user = MagicMock()
        request.user.is_authenticated = True
        request.user.role = role
    else:
        request.user = MagicMock()
        request.user.is_authenticated = False
        request.user.role = None
    return request


def make_view(payroll_action=False):
    """Helper: build a mock DRF view with optional payroll_action flag."""
    view = MagicMock()
    view.payroll_action = payroll_action
    return view


class HRPermissionUnauthenticatedTest(TestCase):
    """Edge case: unauthenticated requests must always be denied."""

    def setUp(self):
        self.permission = HRPermission()

    def test_unauthenticated_has_permission_denied(self):
        request = make_request(authenticated=False)
        view = make_view()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_unauthenticated_has_object_permission_denied(self):
        request = make_request(authenticated=False)
        view = make_view()
        obj = MagicMock()
        self.assertFalse(self.permission.has_object_permission(request, view, obj))

    def test_none_user_has_permission_denied(self):
        """request.user is None (should not raise, should return False)."""
        request = MagicMock()
        request.user = None
        view = make_view()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_none_user_has_object_permission_denied(self):
        request = MagicMock()
        request.user = None
        view = make_view()
        obj = MagicMock()
        self.assertFalse(self.permission.has_object_permission(request, view, obj))


class HRPermissionUnknownRoleTest(TestCase):
    """Edge case: unknown/unrecognised role must always be denied."""

    def setUp(self):
        self.permission = HRPermission()

    def test_unknown_role_get_denied(self):
        request = make_request(method='GET', role='unknown_role')
        view = make_view()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_unknown_role_post_denied(self):
        request = make_request(method='POST', role='unknown_role')
        view = make_view()
        self.assertFalse(self.permission.has_permission(request, view))

    def test_unknown_role_object_permission_denied(self):
        request = make_request(method='GET', role='unknown_role')
        view = make_view()
        obj = MagicMock()
        self.assertFalse(self.permission.has_object_permission(request, view, obj))


# ---------------------------------------------------------------------------
# Requirement 11.4 — Salesman: blocked on ALL methods
# ---------------------------------------------------------------------------

class HRPermissionSalesmanTest(TestCase):
    """
    Requirement 11.4: salesman role must be denied access to every HR endpoint
    regardless of HTTP method or view type.
    """

    def setUp(self):
        self.permission = HRPermission()

    def test_salesman_get_blocked(self):
        request = make_request(method='GET', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_head_blocked(self):
        request = make_request(method='HEAD', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_options_blocked(self):
        request = make_request(method='OPTIONS', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_post_blocked(self):
        request = make_request(method='POST', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_put_blocked(self):
        request = make_request(method='PUT', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_patch_blocked(self):
        request = make_request(method='PATCH', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_delete_blocked(self):
        request = make_request(method='DELETE', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view()))

    def test_salesman_payroll_view_blocked(self):
        """Salesman is blocked even on payroll-action views."""
        request = make_request(method='POST', role='salesman')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_salesman_object_permission_blocked(self):
        request = make_request(method='GET', role='salesman')
        obj = MagicMock()
        self.assertFalse(self.permission.has_object_permission(request, make_view(), obj))

    def test_salesman_error_message_set(self):
        """has_permission should set a descriptive message for salesman."""
        request = make_request(method='GET', role='salesman')
        self.permission.has_permission(request, make_view())
        self.assertIn('Salesman', self.permission.message)


# ---------------------------------------------------------------------------
# Requirement 11.1 — Admin: full access
# ---------------------------------------------------------------------------

class HRPermissionAdminTest(TestCase):
    """
    Requirement 11.1: admin role must be allowed all CRUD operations on all
    HR resources including payroll actions.
    """

    def setUp(self):
        self.permission = HRPermission()

    def test_admin_get_allowed(self):
        request = make_request(method='GET', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_admin_post_allowed(self):
        request = make_request(method='POST', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_admin_put_allowed(self):
        request = make_request(method='PUT', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_admin_patch_allowed(self):
        request = make_request(method='PATCH', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_admin_delete_allowed(self):
        request = make_request(method='DELETE', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_admin_payroll_action_allowed(self):
        request = make_request(method='POST', role='admin')
        self.assertTrue(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_admin_object_permission_allowed(self):
        request = make_request(method='DELETE', role='admin')
        obj = MagicMock()
        self.assertTrue(self.permission.has_object_permission(request, make_view(), obj))

    def test_admin_delete_employee_object_allowed(self):
        """Admin can delete Employee instances (unlike manager)."""
        from hr.models import Employee
        request = make_request(method='DELETE', role='admin')
        # Use a real Employee-like object (spec=Employee ensures isinstance check works)
        emp = MagicMock(spec=Employee)
        self.assertTrue(self.permission.has_object_permission(request, make_view(), emp))


# ---------------------------------------------------------------------------
# Requirement 11.2 — Manager: CRU on employee/attendance/leave; no DELETE Employee; no payroll
# ---------------------------------------------------------------------------

class HRPermissionManagerTest(TestCase):
    """
    Requirement 11.2: manager role may create/read/update employee, attendance,
    and leave resources but must be blocked from:
      - DELETE on Employee instances (object-level)
      - Any payroll action (view-level)
    """

    def setUp(self):
        self.permission = HRPermission()

    # --- Safe methods allowed ---

    def test_manager_get_allowed(self):
        request = make_request(method='GET', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_manager_head_allowed(self):
        request = make_request(method='HEAD', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_manager_options_allowed(self):
        request = make_request(method='OPTIONS', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    # --- Mutation methods allowed on non-payroll views ---

    def test_manager_post_allowed(self):
        request = make_request(method='POST', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_manager_put_allowed(self):
        request = make_request(method='PUT', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_manager_patch_allowed(self):
        request = make_request(method='PATCH', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_manager_delete_view_level_allowed(self):
        """
        DELETE passes view-level check for manager (object-level blocks it for Employee).
        Non-Employee resources (e.g. Department) can be deleted by manager via business logic.
        """
        request = make_request(method='DELETE', role='manager')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    # --- Payroll actions blocked ---

    def test_manager_payroll_post_blocked(self):
        """Requirement 11.2: manager cannot initiate payroll runs."""
        request = make_request(method='POST', role='manager')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_manager_payroll_get_blocked(self):
        """Even GET on a payroll_action view is blocked for manager."""
        request = make_request(method='GET', role='manager')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_manager_payroll_patch_blocked(self):
        request = make_request(method='PATCH', role='manager')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_manager_payroll_error_message_set(self):
        request = make_request(method='POST', role='manager')
        self.permission.has_permission(request, make_view(payroll_action=True))
        self.assertIn('Manager', self.permission.message)
        self.assertIn('payroll', self.permission.message.lower())

    # --- Object-level: DELETE on Employee blocked ---

    def test_manager_delete_employee_object_blocked(self):
        """Requirement 11.2: manager cannot delete an Employee instance."""
        from hr.models import Employee
        request = make_request(method='DELETE', role='manager')
        emp = MagicMock(spec=Employee)
        self.assertFalse(self.permission.has_object_permission(request, make_view(), emp))

    def test_manager_delete_employee_error_message_set(self):
        from hr.models import Employee
        request = make_request(method='DELETE', role='manager')
        emp = MagicMock(spec=Employee)
        self.permission.has_object_permission(request, make_view(), emp)
        self.assertIn('Manager', self.permission.message)
        self.assertIn('Employee', self.permission.message)

    def test_manager_delete_non_employee_object_allowed(self):
        """Manager can delete non-Employee objects (e.g. Department) at object level."""
        from hr.models import Department
        request = make_request(method='DELETE', role='manager')
        dept = MagicMock(spec=Department)
        self.assertTrue(self.permission.has_object_permission(request, make_view(), dept))

    def test_manager_get_employee_object_allowed(self):
        """Manager can read Employee objects."""
        from hr.models import Employee
        request = make_request(method='GET', role='manager')
        emp = MagicMock(spec=Employee)
        self.assertTrue(self.permission.has_object_permission(request, make_view(), emp))

    def test_manager_patch_employee_object_allowed(self):
        """Manager can update Employee objects."""
        from hr.models import Employee
        request = make_request(method='PATCH', role='manager')
        emp = MagicMock(spec=Employee)
        self.assertTrue(self.permission.has_object_permission(request, make_view(), emp))


# ---------------------------------------------------------------------------
# Requirement 11.3 — Accountant: read-only + payroll actions; no employee/attendance/leave mutations
# ---------------------------------------------------------------------------

class HRPermissionAccountantTest(TestCase):
    """
    Requirement 11.3: accountant role may read all HR resources and perform
    payroll actions, but must be blocked from creating, updating, or deleting
    employee profiles, attendance records, or leave applications.
    """

    def setUp(self):
        self.permission = HRPermission()

    # --- Safe methods allowed ---

    def test_accountant_get_allowed(self):
        request = make_request(method='GET', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_accountant_head_allowed(self):
        request = make_request(method='HEAD', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    def test_accountant_options_allowed(self):
        request = make_request(method='OPTIONS', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view()))

    # --- Payroll actions allowed ---

    def test_accountant_payroll_post_allowed(self):
        """Requirement 11.3: accountant can initiate payroll runs."""
        request = make_request(method='POST', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_accountant_payroll_patch_allowed(self):
        """Accountant can finalise (PATCH) payroll runs."""
        request = make_request(method='PATCH', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view(payroll_action=True)))

    def test_accountant_payroll_put_allowed(self):
        request = make_request(method='PUT', role='accountant')
        self.assertTrue(self.permission.has_permission(request, make_view(payroll_action=True)))

    # --- Employee/attendance/leave mutations blocked ---

    def test_accountant_post_non_payroll_blocked(self):
        """Requirement 11.3: accountant cannot create employee/attendance/leave records."""
        request = make_request(method='POST', role='accountant')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=False)))

    def test_accountant_put_non_payroll_blocked(self):
        request = make_request(method='PUT', role='accountant')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=False)))

    def test_accountant_patch_non_payroll_blocked(self):
        request = make_request(method='PATCH', role='accountant')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=False)))

    def test_accountant_delete_non_payroll_blocked(self):
        request = make_request(method='DELETE', role='accountant')
        self.assertFalse(self.permission.has_permission(request, make_view(payroll_action=False)))

    def test_accountant_mutation_error_message_set(self):
        request = make_request(method='POST', role='accountant')
        self.permission.has_permission(request, make_view(payroll_action=False))
        self.assertIn('Accountant', self.permission.message)

    # --- Object-level: safe methods allowed ---

    def test_accountant_get_object_allowed(self):
        request = make_request(method='GET', role='accountant')
        obj = MagicMock()
        self.assertTrue(self.permission.has_object_permission(request, make_view(), obj))

    # --- Object-level: payroll action mutations allowed ---

    def test_accountant_payroll_object_post_allowed(self):
        request = make_request(method='POST', role='accountant')
        obj = MagicMock()
        self.assertTrue(
            self.permission.has_object_permission(request, make_view(payroll_action=True), obj)
        )

    # --- Object-level: non-payroll mutations blocked ---

    def test_accountant_non_payroll_object_post_blocked(self):
        request = make_request(method='POST', role='accountant')
        obj = MagicMock()
        self.assertFalse(
            self.permission.has_object_permission(request, make_view(payroll_action=False), obj)
        )

    def test_accountant_non_payroll_object_delete_blocked(self):
        request = make_request(method='DELETE', role='accountant')
        obj = MagicMock()
        self.assertFalse(
            self.permission.has_object_permission(request, make_view(payroll_action=False), obj)
        )


# ---------------------------------------------------------------------------
# Cross-role sanity checks
# ---------------------------------------------------------------------------

class HRPermissionCrossRoleTest(TestCase):
    """
    Sanity checks that confirm the permission matrix is consistent across roles
    for the same operation.
    """

    def setUp(self):
        self.permission = HRPermission()

    def test_only_admin_and_accountant_can_run_payroll(self):
        """POST on a payroll_action view: admin ✓, accountant ✓, manager ✗, salesman ✗."""
        view = make_view(payroll_action=True)
        results = {}
        for role in ('admin', 'manager', 'accountant', 'salesman'):
            request = make_request(method='POST', role=role)
            results[role] = self.permission.has_permission(request, view)

        self.assertTrue(results['admin'])
        self.assertTrue(results['accountant'])
        self.assertFalse(results['manager'])
        self.assertFalse(results['salesman'])

    def test_only_admin_can_delete_employee_object(self):
        """DELETE on Employee object: admin ✓, manager ✗, accountant ✗, salesman ✗."""
        from hr.models import Employee
        emp = MagicMock(spec=Employee)
        view = make_view()
        results = {}
        for role in ('admin', 'manager', 'accountant', 'salesman'):
            request = make_request(method='DELETE', role=role)
            results[role] = self.permission.has_object_permission(request, view, emp)

        self.assertTrue(results['admin'])
        self.assertFalse(results['manager'])
        self.assertFalse(results['accountant'])
        self.assertFalse(results['salesman'])

    def test_all_roles_except_salesman_can_read(self):
        """GET on a regular view: admin ✓, manager ✓, accountant ✓, salesman ✗."""
        view = make_view()
        results = {}
        for role in ('admin', 'manager', 'accountant', 'salesman'):
            request = make_request(method='GET', role=role)
            results[role] = self.permission.has_permission(request, view)

        self.assertTrue(results['admin'])
        self.assertTrue(results['manager'])
        self.assertTrue(results['accountant'])
        self.assertFalse(results['salesman'])

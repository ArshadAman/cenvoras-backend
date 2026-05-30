# HR custom DRF permission classes — implemented in task 3.3

from rest_framework import permissions

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


class HRPermission(permissions.BasePermission):
    """
    Role-based permission class for the HR module.

    Role matrix:
    - salesman   → always denied (HTTP 403)
    - admin      → always allowed
    - manager    → safe methods + CRU on employee/attendance/leave;
                   no DELETE on Employee; no payroll actions
    - accountant → safe methods + payroll actions only
                   (views where payroll_action = True)

    Requirements: 11.1, 11.2, 11.3, 11.4, 11.5
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        role = request.user.role

        # Salesman: no access to any HR endpoint
        if role == 'salesman':
            self.message = "Salesman role is not permitted to access HR resources."
            return False

        # Admin: full access
        if role == 'admin':
            return True

        # Determine if this view is a payroll action
        is_payroll_action = getattr(view, 'payroll_action', False)

        # Manager: safe methods + CRU on employee/attendance/leave; no payroll actions
        if role == 'manager':
            if is_payroll_action:
                self.message = "Manager role is not permitted to perform payroll actions."
                return False
            # Allow safe methods and non-DELETE mutations
            if request.method in SAFE_METHODS:
                return True
            if request.method == 'DELETE':
                # DELETE is evaluated per-object in has_object_permission;
                # at view level we allow it to pass through so object-level
                # check can block it for Employee specifically.
                # For non-Employee resources managers can delete (e.g. dept/designation
                # deletion is handled by business logic, not RBAC).
                return True
            # POST, PUT, PATCH — allowed for manager
            return True

        # Accountant: safe methods + payroll actions only
        if role == 'accountant':
            if request.method in SAFE_METHODS:
                return True
            if is_payroll_action:
                return True
            self.message = (
                "Accountant role may only perform read operations or payroll actions "
                "on HR resources."
            )
            return False

        # HR: full access
        if role == 'hr':
            return True

        # Employee: limited access to specific viewsets
        if role == 'employee':
            allowed_views = ['LeaveApplicationViewSet', 'AttendanceViewSet', 'LeaveBalanceViewSet', 'EmployeeTaskViewSet', 'EmployeeQueryViewSet', 'LeaveTypeViewSet', 'EmployeeViewSet']
            if view.__class__.__name__ in allowed_views:
                if request.method == 'DELETE':
                    self.message = "Employees cannot delete records."
                    return False
                return True
            self.message = "Employee role is not permitted to access this resource."
            return False

        # Unknown role — deny
        self.message = "Unknown role. Access denied."
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        role = request.user.role

        # Salesman: never
        if role == 'salesman':
            return False

        # Admin and HR: always
        if role in ['admin', 'hr']:
            return True

        # Employee: allow access to their own records via viewset get_queryset filtering,
        # here we just allow if they passed the view-level check, except for DELETE.
        if role == 'employee':
            if request.method == 'DELETE':
                return False
            return True

        # Manager: block DELETE on Employee instances
        if role == 'manager':
            from hr.models import Employee  # local import to avoid circular deps
            if request.method == 'DELETE' and isinstance(obj, Employee):
                self.message = "Manager role is not permitted to delete Employee records."
                return False
            return True

        # Accountant: safe methods + payroll actions
        is_payroll_action = getattr(view, 'payroll_action', False)
        if role == 'accountant':
            if request.method in SAFE_METHODS:
                return True
            if is_payroll_action:
                return True
            self.message = (
                "Accountant role may only perform read operations or payroll actions "
                "on HR resources."
            )
            return False

        return False

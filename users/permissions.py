from rest_framework import permissions

class IsAdminUser(permissions.BasePermission):
    """
    Allows access only to admin users.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == 'admin')

class IsManagerOrAdmin(permissions.BasePermission):
    """
    Allows access to managers and admins.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ['admin', 'manager'])

class IsSalesmanOrAbove(permissions.BasePermission):
    """
    Allows access to salesman, managers, and admins.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ['admin', 'manager', 'salesman'])

class IsAccountantOrAbove(permissions.BasePermission):
    """
    Allows access to accountants, managers, and admins.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ['admin', 'manager', 'accountant'])

from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication


def _resolve_permission_level(permissions, module):
    alias_map = {
        'sales': ['sales'],
        'purchases': ['purchases', 'purchase'],
        'inventory': ['inventory'],
        'financials': ['financials', 'finance', 'financial'],
    }
    for key in alias_map.get(module, [module]):
        value = permissions.get(key)
        if value in {'none', 'view', 'edit'}:
            return value
    return 'none'

class ManagerPermissionMiddleware:
    """
    Enforces granular permissions for users with the 'manager' role globally.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_auth = JWTAuthentication()

    def __call__(self, request):
        path = request.path
        if not path.startswith('/api/'):
            return self.get_response(request)
            
        bypass = ['/api/users/', '/api/subscription/', '/api/ai/', '/api/integration/']
        if any(path.startswith(prefix) for prefix in bypass):
            return self.get_response(request)

        # Attempt to authenticate if not already done by Django session auth
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            try:
                auth_result = self.jwt_auth.authenticate(request)
                if auth_result:
                    request.user = auth_result[0]
            except Exception:
                pass

        if hasattr(request, 'user') and request.user.is_authenticated and getattr(request.user, 'role', '') != 'admin':
            permissions = getattr(request.user, 'permissions', {}) or {}
            if not isinstance(permissions, dict):
                permissions = {}
            
            module = None
            
            if path.startswith('/api/billing/sales-invoices') or \
               path.startswith('/api/billing/customers') or \
               path.startswith('/api/billing/quotations') or \
               path.startswith('/api/billing/sales-orders') or \
               path.startswith('/api/billing/credit-notes') or \
               path.startswith('/api/billing/delivery-challans') or \
               path.startswith('/api/billing/invoice-settings'):
                module = 'sales'
            elif path.startswith('/api/billing/purchase-bills') or \
                 path.startswith('/api/billing/vendors') or \
                 path.startswith('/api/billing/vendor-products') or \
                 path.startswith('/api/billing/debit-notes'):
                module = 'purchases'
            elif path.startswith('/api/inventory'):
                module = 'inventory'
            elif path.startswith('/api/ledger') or \
                 path.startswith('/api/billing/payments') or \
                 path.startswith('/api/billing/gst') or \
                 path.startswith('/api/billing/reports') or \
                 path.startswith('/api/reports') or \
                 path.startswith('/api/analytics'):
                module = 'financials'
            
            if module:
                perm_level = _resolve_permission_level(permissions, module)
                
                if perm_level == 'none':
                    return JsonResponse({'success': False, 'error': f"You don't have permission to access the {module} module."}, status=403)
                elif perm_level == 'view' and request.method not in ['GET', 'HEAD', 'OPTIONS']:
                    return JsonResponse({'success': False, 'error': f"You only have view access for the {module} module."}, status=403)
                
        return self.get_response(request)

class RegionalContextMiddleware:
    """
    Dynamically sets the active timezone and attaches country/currency to the request
    based on the authenticated user's shop profile.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.utils import timezone
        
        # Default fallback
        request.country = 'IN'
        request.currency = 'INR'
        
        if hasattr(request, 'user') and request.user.is_authenticated:
            # The active_tenant property gets the parent if the user is a team member
            tenant = getattr(request.user, 'active_tenant', request.user)
            country = getattr(tenant, 'country', 'IN')
            currency = getattr(tenant, 'currency', 'INR')
            
            request.country = country
            request.currency = currency
            
            if country == 'AE':
                timezone.activate('Asia/Dubai')
            else:
                timezone.activate('Asia/Kolkata')
        
        response = self.get_response(request)
        
        # Cleanup
        from django.utils import timezone
        timezone.deactivate()
        
        return response

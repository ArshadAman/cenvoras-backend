from django.http import JsonResponse

from .services import can_use_feature, get_effective_plan_code


class SubscriptionAccessMiddleware:
    """
    Enforces plan-gated API access on the backend so frontend-only locks cannot be bypassed.
    """

    # Free plan: only these API surfaces stay unlocked.
    FREE_ALLOWED_PREFIXES = (
        '/api/subscription/',
        '/api/users/profile/',
        '/api/users/profile/update/',
        '/api/users/profile/setup/',
        '/api/billing/sales-invoices/',
        '/api/billing/customers/',
        '/api/billing/payments/',
    )

    # Route-based feature checks (ordered from specific to generic).
    FEATURE_RULES = (
        ('/api/analytics/ml-predictions/', 'sales_forecast'),
        ('/api/inventory/warehouses/', 'multi_warehouse'),
        ('/api/reports/profit-loss/', 'item_wise_pnl'),
        ('/api/reports/stock-ledger/', 'stock_ledger'),
        ('/api/reports/shortage/', 'shortage_management'),
        ('/api/reports/', 'advanced_reports'),
        ('/api/integration/', 'integrations'),
        ('/api/analytics/', 'advanced_analytics'),
        ('/api/inventory/', 'inventory_core'),
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _normalize(path: str) -> str:
        if not path:
            return '/'
        return path if path.endswith('/') else f'{path}/'

    @classmethod
    def _path_matches_prefix(cls, path: str, prefix: str) -> bool:
        return cls._normalize(path).startswith(cls._normalize(prefix))

    def _free_route_allowed(self, path: str) -> bool:
        return any(self._path_matches_prefix(path, prefix) for prefix in self.FREE_ALLOWED_PREFIXES)

    def _required_feature_for_path(self, path: str):
        for prefix, feature_code in self.FEATURE_RULES:
            if self._path_matches_prefix(path, prefix):
                return feature_code
        return None

    def _forbidden(self, code: str, message: str, path: str):
        return JsonResponse(
            {
                'error': 'Access denied by subscription policy.',
                'code': code,
                'message': message,
                'path': path,
            },
            status=403,
        )

    def __call__(self, request):
        path = request.path or '/'

        # Non-API traffic is ignored.
        if not path.startswith('/api/'):
            return self.get_response(request)

        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        if getattr(user, 'is_superuser', False):
            return self.get_response(request)

        plan_code = get_effective_plan_code(user)

        # Free plan hard gate.
        if plan_code == 'free' and not self._free_route_allowed(path):
            return self._forbidden(
                code='plan_locked',
                message='Your Free plan can access only Sales Invoices, Customers, Payments, and Profile.',
                path=path,
            )

        # Feature gate for all paid tiers (and free where applicable).
        required_feature = self._required_feature_for_path(path)
        if required_feature and not can_use_feature(user, required_feature):
            return self._forbidden(
                code='feature_locked',
                message=f'This route requires feature: {required_feature}.',
                path=path,
            )

        return self.get_response(request)

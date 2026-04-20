from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase
import json
from unittest.mock import patch

from .middleware import SubscriptionAccessMiddleware
from . import tasks


class _UserStub:
    def __init__(self, username='user', *, is_superuser=False, is_lifetime_free=False):
        self.username = username
        self.is_authenticated = True
        self.is_superuser = is_superuser
        self.is_lifetime_free = is_lifetime_free


class TestSubscriptionAccessMiddleware(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SubscriptionAccessMiddleware(lambda request: JsonResponse({'ok': True}, status=200))
        self.free_user = _UserStub(username='free-user')
        self.pro_user = _UserStub(username='pro-user')
        self.vip_user = _UserStub(username='vip-user', is_lifetime_free=True)

    def _request(self, path, user):
        request = self.factory.get(path)
        request.user = user
        return self.middleware(request)

    @staticmethod
    def _json(response):
        return json.loads(response.content.decode('utf-8'))

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=True)
    def test_free_plan_allows_sales_invoice_routes(self, _can_use_feature, _get_plan):
        response = self._request('/api/billing/sales-invoices/', self.free_user)
        self.assertEqual(response.status_code, 200)

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=True)
    def test_free_plan_allows_payments_routes(self, _can_use_feature, _get_plan):
        response = self._request('/api/billing/payments/', self.free_user)
        self.assertEqual(response.status_code, 200)

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=True)
    def test_free_plan_blocks_inventory_routes(self, _can_use_feature, _get_plan):
        response = self._request('/api/inventory/products/', self.free_user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._json(response).get('code'), 'plan_locked')

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=True)
    def test_free_plan_blocks_sales_order_routes(self, _can_use_feature, _get_plan):
        response = self._request('/api/billing/sales-orders/', self.free_user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._json(response).get('code'), 'plan_locked')

    @patch('subscription.middleware.get_effective_plan_code', return_value='pro')
    @patch('subscription.middleware.can_use_feature', side_effect=lambda _user, feature: feature != 'multi_warehouse')
    def test_pro_plan_allows_inventory_but_blocks_warehouse(self, _can_use_feature, _get_plan):
        inventory_response = self._request('/api/inventory/products/', self.pro_user)
        warehouse_response = self._request('/api/inventory/warehouses/', self.pro_user)

        self.assertEqual(inventory_response.status_code, 200)
        self.assertEqual(warehouse_response.status_code, 403)
        self.assertEqual(self._json(warehouse_response).get('code'), 'feature_locked')

    @patch('subscription.middleware.get_effective_plan_code', return_value='pro')
    @patch('subscription.middleware.can_use_feature', side_effect=lambda _user, feature: feature != 'sales_forecast')
    def test_pro_plan_blocks_ml_predictions_business_feature(self, _can_use_feature, _get_plan):
        response = self._request('/api/analytics/ml-predictions/', self.pro_user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._json(response).get('code'), 'feature_locked')

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=False)
    def test_vip_user_bypasses_plan_lock(self, _can_use_feature, _get_plan):
        response = self._request('/api/inventory/products/', self.vip_user)
        self.assertEqual(response.status_code, 200)

    @patch('subscription.middleware.get_effective_plan_code', return_value='free')
    @patch('subscription.middleware.can_use_feature', return_value=False)
    def test_vip_user_bypasses_feature_lock(self, _can_use_feature, _get_plan):
        response = self._request('/api/analytics/ml-predictions/', self.vip_user)
        self.assertEqual(response.status_code, 200)


class TestSubscriptionWebhookFollowup(SimpleTestCase):
    @patch('subscription.tasks._handle_payment_failed', return_value={'status': 'failed'})
    @patch('subscription.tasks._handle_payment_success', return_value={'status': 'success'})
    @patch('subscription.tasks._fetch_payment_attempts')
    @patch('subscription.tasks.SubscriptionPayment.objects.select_related')
    def test_pending_followup_marks_failed_on_terminal_failure(
        self,
        mock_select_related,
        mock_fetch_attempts,
        _mock_success,
        mock_failed,
    ):
        payment = type('PaymentStub', (), {'status': 'pending'})()
        mock_select_related.return_value.get.return_value = payment
        mock_fetch_attempts.return_value = [
            {'payment_status': 'PENDING'},
            {'payment_status': 'FAILED'},
        ]

        result = tasks.verify_pending_payment_from_webhook.run(order_id='order-123')

        self.assertEqual(result['status'], 'failed')
        self.assertTrue(mock_failed.called)

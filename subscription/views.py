import uuid
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import (
	Plan,
	TenantSubscription,
	SubscriptionStatus,
	SubscriptionPayment,
	SubscriptionPaymentStatus,
)
from .services import get_entitlements


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_entitlements(request):
	return Response({
		'success': True,
		'data': get_entitlements(request.user),
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def plan_catalog(request):
	plans = Plan.objects.filter(is_active=True).prefetch_related('features').order_by('monthly_price', 'name')
	data = []
	for plan in plans:
		data.append({
			'id': str(plan.id),
			'code': plan.code,
			'name': plan.name,
			'description': plan.description,
			'monthly_price': str(plan.monthly_price),
			'yearly_price': str(plan.yearly_price),
			'max_managers': plan.max_managers,
			'max_team_members': getattr(plan, 'max_team_members', plan.max_managers),
			'max_customers': getattr(plan, 'max_customers', -1),
			'max_invoices_per_month': plan.max_invoices_per_month,
			'features': [feature.code for feature in plan.features.all()],
		})

	return Response({
		'success': True,
		'data': data,
	})


def _cashfree_base_url():
	env = (getattr(settings, 'CASHFREE_ENV', 'sandbox') or 'sandbox').lower()
	return 'https://api.cashfree.com/pg' if env == 'production' else 'https://sandbox.cashfree.com/pg'


def _cashfree_headers():
	return {
		'Content-Type': 'application/json',
		'x-api-version': getattr(settings, 'CASHFREE_API_VERSION', '2023-08-01'),
		'x-client-id': getattr(settings, 'CASHFREE_CLIENT_ID', ''),
		'x-client-secret': getattr(settings, 'CASHFREE_CLIENT_SECRET', ''),
	}


def _sync_legacy_subscription_fields(tenant, plan_code):
	tier_map = {
		'free': 'FREE',
		'pro': 'MID',
		'business': 'PRO',
	}
	tenant.subscription_status = 'active'
	tenant.subscription_tier = tier_map.get(plan_code, 'FREE')
	tenant.save(update_fields=['subscription_status', 'subscription_tier'])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_plan_payment_order(request):
	tenant = getattr(request.user, 'active_tenant', request.user)

	if request.user.id != tenant.id:
		return Response({
			'success': False,
			'error': 'Only tenant admin can create plan payments.'
		}, status=status.HTTP_403_FORBIDDEN)

	plan_code = str(request.data.get('plan_code', '')).strip().lower()
	if plan_code not in {'pro', 'business'}:
		return Response({
			'success': False,
			'error': 'Only pro and business plans are payable.'
		}, status=status.HTTP_400_BAD_REQUEST)

	plan = Plan.objects.filter(code=plan_code, is_active=True).first()
	if not plan:
		return Response({'success': False, 'error': 'Plan not found.'}, status=status.HTTP_404_NOT_FOUND)

	if not settings.CASHFREE_CLIENT_ID or not settings.CASHFREE_CLIENT_SECRET:
		return Response({
			'success': False,
			'error': 'Cashfree credentials are not configured on server.'
		}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

	order_id = f"sub_{str(tenant.id).replace('-', '')[:8]}_{uuid.uuid4().hex[:18]}"
	request_id = str(uuid.uuid4())
	idempotency_key = str(uuid.uuid4())

	body = {
		'order_id': order_id,
		'order_currency': 'INR',
		'order_amount': float(plan.monthly_price),
		'customer_details': {
			'customer_id': str(tenant.id),
			'customer_name': tenant.business_name or tenant.username or 'Cenvora User',
			'customer_email': tenant.email or 'support@cenvora.app',
			'customer_phone': tenant.phone or '9999999999',
		},
		'order_note': f'Plan upgrade to {plan.name}',
		'order_meta': {
			'return_url': f"{getattr(settings, 'CASHFREE_RETURN_URL', 'https://cenvora.app/profile')}?order_id={order_id}",
		},
	}

	headers = _cashfree_headers()
	headers['x-request-id'] = request_id
	headers['x-idempotency-key'] = idempotency_key

	response = requests.post(
		f"{_cashfree_base_url()}/orders",
		json=body,
		headers=headers,
		timeout=20,
	)

	try:
		response_data = response.json()
	except Exception:
		response_data = {'raw': response.text}

	if response.status_code >= 400:
		return Response({
			'success': False,
			'error': 'Failed to create Cashfree order.',
			'details': response_data,
		}, status=status.HTTP_400_BAD_REQUEST)

	SubscriptionPayment.objects.create(
		tenant=tenant,
		plan=plan,
		provider='cashfree',
		order_id=order_id,
		cf_order_id=response_data.get('cf_order_id'),
		payment_session_id=response_data.get('payment_session_id'),
		amount=plan.monthly_price,
		currency='INR',
		status=SubscriptionPaymentStatus.PENDING,
		raw_response=response_data,
	)

	return Response({
		'success': True,
		'data': {
			'order_id': order_id,
			'cf_order_id': response_data.get('cf_order_id'),
			'payment_session_id': response_data.get('payment_session_id'),
			'plan_code': plan.code,
			'amount': str(plan.monthly_price),
			'currency': 'INR',
		}
	})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_plan_payment(request):
	tenant = getattr(request.user, 'active_tenant', request.user)
	order_id = str(request.data.get('order_id', '')).strip()

	if not order_id:
		return Response({'success': False, 'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

	payment = SubscriptionPayment.objects.filter(order_id=order_id, tenant=tenant).select_related('plan').first()
	if not payment:
		return Response({'success': False, 'error': 'Payment order not found.'}, status=status.HTTP_404_NOT_FOUND)

	headers = _cashfree_headers()
	response = requests.get(
		f"{_cashfree_base_url()}/orders/{order_id}/payments",
		headers=headers,
		timeout=20,
	)

	try:
		payments_data = response.json()
	except Exception:
		payments_data = []

	if response.status_code >= 400:
		return Response({
			'success': False,
			'error': 'Unable to verify payment with Cashfree.',
			'details': payments_data,
		}, status=status.HTTP_400_BAD_REQUEST)

	if isinstance(payments_data, dict):
		payments_data = payments_data.get('data', []) if isinstance(payments_data.get('data', []), list) else []

	success_payment = None
	for attempt in payments_data:
		if attempt.get('payment_status') == 'SUCCESS':
			success_payment = attempt
			break

	if not success_payment:
		any_pending = any(item.get('payment_status') in {'PENDING', 'NOT_ATTEMPTED'} for item in payments_data)
		if not any_pending:
			payment.status = SubscriptionPaymentStatus.FAILED
			payment.raw_response = {'attempts': payments_data}
			payment.save(update_fields=['status', 'raw_response', 'updated_at'])

		return Response({
			'success': False,
			'data': {
				'order_id': order_id,
				'status': payment.status,
				'message': 'Payment not successful yet.'
			}
		})

	now = timezone.now()
	payment.status = SubscriptionPaymentStatus.SUCCESS
	payment.cf_payment_id = success_payment.get('cf_payment_id')
	payment.paid_at = now
	payment.raw_response = {'attempts': payments_data, 'successful_attempt': success_payment}
	payment.save(update_fields=['status', 'cf_payment_id', 'paid_at', 'raw_response', 'updated_at'])

	subscription, _created = TenantSubscription.objects.get_or_create(
		tenant=tenant,
		defaults={
			'plan': payment.plan,
			'status': SubscriptionStatus.ACTIVE,
			'current_period_start': now,
			'current_period_end': now + timedelta(days=30),
			'cancel_at_period_end': False,
			'pending_plan': None,
			'pending_plan_starts_at': None,
		},
	)

	queued = False
	queued_starts_at = None

	active_until = subscription.current_period_end if subscription.current_period_end and subscription.current_period_end > now else None

	if active_until:
		if subscription.plan_id == payment.plan_id and not subscription.pending_plan_id:
			# Same plan recharge before expiry: extend current period directly.
			subscription.current_period_end = active_until + timedelta(days=30)
			subscription.status = SubscriptionStatus.ACTIVE
			subscription.cancel_at_period_end = False
			subscription.save(update_fields=['current_period_end', 'status', 'cancel_at_period_end', 'updated_at'])
			_sync_legacy_subscription_fields(tenant, subscription.plan.code)
		elif subscription.plan_id != payment.plan_id:
			# Different plan recharge before expiry: queue new plan after current period.
			subscription.pending_plan = payment.plan
			subscription.pending_plan_starts_at = active_until
			subscription.status = SubscriptionStatus.ACTIVE
			subscription.cancel_at_period_end = False
			subscription.save(update_fields=['pending_plan', 'pending_plan_starts_at', 'status', 'cancel_at_period_end', 'updated_at'])
			queued = True
			queued_starts_at = active_until
			_sync_legacy_subscription_fields(tenant, subscription.plan.code)
		else:
			# Pending renewal already exists for same plan; keep latest successful payment recorded.
			subscription.status = SubscriptionStatus.ACTIVE
			subscription.save(update_fields=['status', 'updated_at'])
			_sync_legacy_subscription_fields(tenant, subscription.plan.code)
	else:
		# Expired or no active cycle: activate immediately for 30 days.
		subscription.plan = payment.plan
		subscription.status = SubscriptionStatus.ACTIVE
		subscription.current_period_start = now
		subscription.current_period_end = now + timedelta(days=30)
		subscription.cancel_at_period_end = False
		subscription.pending_plan = None
		subscription.pending_plan_starts_at = None
		subscription.save(update_fields=[
			'plan', 'status', 'current_period_start', 'current_period_end',
			'cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'
		])
		_sync_legacy_subscription_fields(tenant, payment.plan.code)

	return Response({
		'success': True,
		'data': {
			'order_id': order_id,
			'plan_code': subscription.plan.code,
			'queued_plan_code': subscription.pending_plan.code if subscription.pending_plan else None,
			'queued': queued,
			'queued_starts_at': queued_starts_at,
			'subscription_status': subscription.status,
			'current_period_end': subscription.current_period_end,
		}
	})

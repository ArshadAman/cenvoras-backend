import uuid
import json
import logging
import hashlib
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
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
	SubscriptionPaymentAction,
	WebhookEvent,
)
from .services import get_entitlements
from .tasks import process_cashfree_webhook, verify_cashfree_signature

logger = logging.getLogger(__name__)


def _normalize_webhook_event_type(raw_event_type: str) -> str:
	event_type = (raw_event_type or '').upper().strip()
	if event_type.endswith('_WEBHOOK'):
		event_type = event_type.replace('_WEBHOOK', '')
	if event_type in {'PAYMENT_SUCCEEDED', 'PAYMENT_SUCCESSFUL'}:
		return 'PAYMENT_SUCCESS'
	if event_type in {'PAYMENT_FAILURE', 'PAYMENT_FAILED'}:
		return 'PAYMENT_FAILED'
	if event_type in {'PAYMENT_PENDING'}:
		return 'PAYMENT_PENDING'
	return event_type


def _extract_order_id(payload: dict) -> str | None:
	if not isinstance(payload, dict):
		return None

	data = payload.get('data', {}) if isinstance(payload.get('data', {}), dict) else {}
	order = data.get('order', {}) if isinstance(data.get('order', {}), dict) else {}
	payment = data.get('payment', {}) if isinstance(data.get('payment', {}), dict) else {}

	order_id = (
		data.get('orderId')
		or data.get('order_id')
		or order.get('order_id')
		or order.get('orderId')
		or payment.get('order_id')
		or payment.get('orderId')
		or payload.get('orderId')
		or payload.get('order_id')
	)

	return str(order_id).strip() if order_id else None


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


def _plan_rank(plan_code: str) -> int:
	code = (plan_code or '').lower()
	if code == 'business':
		return 2
	if code == 'pro':
		return 1
	return 0


def _quantize_money(value: Decimal) -> Decimal:
	return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _calculate_plan_change_quote(subscription, target_plan, now):
	current_plan = subscription.plan if subscription else None
	current_code = getattr(current_plan, 'code', 'free')
	target_code = target_plan.code
	active_until = None
	if subscription and subscription.current_period_end and subscription.current_period_end > now:
		active_until = subscription.current_period_end

	current_rank = _plan_rank(current_code)
	target_rank = _plan_rank(target_code)

	if target_code == current_code and active_until:
		return {
			'payment_required': True,
			'action': SubscriptionPaymentAction.RENEW,
			'apply_immediately': False,
			'amount': _quantize_money(target_plan.monthly_price),
			'source_plan_code': current_code,
			'summary': f"Renew {target_plan.name} for 30 more days.",
		}

	if target_rank > current_rank and active_until and current_rank > 0:
		total_seconds = max(int((subscription.current_period_end - subscription.current_period_start).total_seconds()), 1)
		remaining_seconds = max(int((active_until - now).total_seconds()), 0)
		remaining_ratio = Decimal(remaining_seconds) / Decimal(total_seconds)
		delta = max(Decimal('0.00'), Decimal(target_plan.monthly_price) - Decimal(current_plan.monthly_price))
		amount = _quantize_money(delta * remaining_ratio)
		if amount < Decimal('1.00'):
			amount = Decimal('1.00')
		return {
			'payment_required': True,
			'action': SubscriptionPaymentAction.UPGRADE_NOW,
			'apply_immediately': True,
			'amount': amount,
			'source_plan_code': current_code,
			'summary': f"Upgrade now to {target_plan.name}. Remaining-cycle prorated charge applies.",
		}

	if target_rank > current_rank:
		return {
			'payment_required': True,
			'action': SubscriptionPaymentAction.ACTIVATE,
			'apply_immediately': True,
			'amount': _quantize_money(target_plan.monthly_price),
			'source_plan_code': current_code,
			'summary': f"Activate {target_plan.name} immediately for 30 days.",
		}

	if active_until:
		if target_rank == 0:
			return {
				'payment_required': False,
				'action': 'schedule_free',
				'apply_immediately': False,
				'amount': Decimal('0.00'),
				'source_plan_code': current_code,
				'effective_at': active_until,
				'summary': 'Will move to Free plan after current cycle ends.',
			}
		return {
			'payment_required': False,
			'action': 'unsupported_paid_schedule',
			'apply_immediately': False,
			'amount': Decimal('0.00'),
			'source_plan_code': current_code,
			'summary': (
				f"Paid plan changes to {target_plan.name} cannot be scheduled without payment. "
				"Choose Free at expiry, then activate the target plan with payment."
			),
		}

	if target_rank == 0:
		return {
			'payment_required': False,
			'action': 'already_free',
			'apply_immediately': False,
			'amount': Decimal('0.00'),
			'source_plan_code': current_code,
			'summary': 'You are already on Free plan.',
		}

	return {
		'payment_required': True,
		'action': SubscriptionPaymentAction.ACTIVATE,
		'apply_immediately': True,
		'amount': _quantize_money(target_plan.monthly_price),
		'source_plan_code': current_code,
		'summary': f"Activate {target_plan.name} immediately for 30 days.",
	}


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_change_quote(request):
	tenant = getattr(request.user, 'active_tenant', request.user)

	if request.user.id != tenant.id:
		return Response({
			'success': False,
			'error': 'Only tenant admin can manage plan changes.'
		}, status=status.HTTP_403_FORBIDDEN)

	target_code = str(request.data.get('target_plan_code', '')).strip().lower()
	if target_code not in {'free', 'pro', 'business'}:
		return Response({'success': False, 'error': 'target_plan_code must be free, pro or business.'}, status=status.HTTP_400_BAD_REQUEST)

	target_plan = Plan.objects.filter(code=target_code, is_active=True).first() if target_code != 'free' else None
	if target_code != 'free' and not target_plan:
		return Response({'success': False, 'error': 'Target plan not found.'}, status=status.HTTP_404_NOT_FOUND)

	now = timezone.now()
	subscription = TenantSubscription.objects.filter(tenant=tenant).select_related('plan').first()
	if not target_plan:
		free_plan = Plan(code='free', name='Free', monthly_price=Decimal('0.00'))
		quote = _calculate_plan_change_quote(subscription, free_plan, now)
	else:
		quote = _calculate_plan_change_quote(subscription, target_plan, now)

	return Response({
		'success': True,
		'data': {
			'target_plan_code': target_code,
			'target_plan_name': target_plan.name if target_plan else 'Free',
			'payment_required': quote['payment_required'],
			'action': quote['action'],
			'apply_immediately': quote.get('apply_immediately', False),
			'amount': str(quote['amount']),
			'effective_at': quote.get('effective_at'),
			'summary': quote.get('summary', ''),
		}
	})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def schedule_plan_change(request):
	tenant = getattr(request.user, 'active_tenant', request.user)

	if request.user.id != tenant.id:
		return Response({
			'success': False,
			'error': 'Only tenant admin can manage plan changes.'
		}, status=status.HTTP_403_FORBIDDEN)

	target_code = str(request.data.get('target_plan_code', '')).strip().lower()
	if target_code not in {'free', 'pro', 'business'}:
		return Response({'success': False, 'error': 'target_plan_code must be free, pro or business.'}, status=status.HTTP_400_BAD_REQUEST)

	subscription = TenantSubscription.objects.filter(tenant=tenant).select_related('plan').first()
	now = timezone.now()
	if not subscription or not subscription.current_period_end or subscription.current_period_end <= now:
		return Response({'success': False, 'error': 'No active cycle available. Use payment to activate a plan now.'}, status=status.HTTP_400_BAD_REQUEST)

	target_plan = Plan.objects.filter(code=target_code, is_active=True).first() if target_code != 'free' else None
	active_until = subscription.current_period_end

	if target_code == 'free':
		subscription.cancel_at_period_end = True
		subscription.pending_plan = None
		subscription.pending_plan_starts_at = None
		subscription.save(update_fields=['cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'])
		return Response({
			'success': True,
			'data': {
				'action': 'schedule_free',
				'effective_at': active_until,
				'message': 'Plan will move to Free after current cycle ends.',
			}
		})

	if not target_plan:
		return Response({'success': False, 'error': 'Target plan not found.'}, status=status.HTTP_404_NOT_FOUND)

	return Response({
		'success': False,
		'error': 'Only Free plan can be scheduled for next cycle without payment.',
		'data': {
			'action': 'unsupported_paid_schedule',
			'message': 'Activate Pro/Business with payment when current cycle ends.',
		}
	}, status=status.HTTP_400_BAD_REQUEST)


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

	now = timezone.now()
	subscription = TenantSubscription.objects.filter(tenant=tenant).select_related('plan').first()
	quote = _calculate_plan_change_quote(subscription, plan, now)

	if not quote['payment_required']:
		return Response({
			'success': False,
			'error': 'This plan change does not require payment. Use schedule plan change endpoint.',
			'data': {
				'action': quote['action'],
				'effective_at': quote.get('effective_at'),
			}
		}, status=status.HTTP_400_BAD_REQUEST)

	order_amount = quote['amount']
	if order_amount <= Decimal('0.00'):
		return Response({
			'success': False,
			'error': 'No payable amount for this change.',
		}, status=status.HTTP_400_BAD_REQUEST)

	if not settings.CASHFREE_CLIENT_ID or not settings.CASHFREE_CLIENT_SECRET:
		return Response({
			'success': False,
			'error': 'Cashfree credentials are not configured on server.'
		}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

	reuse_window_seconds = max(int(getattr(settings, 'CASHFREE_PAYMENT_ORDER_REUSE_WINDOW_SECONDS', 1800)), 0)
	reuse_cutoff = now - timedelta(seconds=reuse_window_seconds)

	pending_candidates = SubscriptionPayment.objects.filter(
		tenant=tenant,
		plan=plan,
		status=SubscriptionPaymentStatus.PENDING,
		action=quote['action'],
		source_plan_code=quote.get('source_plan_code'),
	).order_by('-created_at')

	for candidate in pending_candidates:
		candidate_details = candidate.billing_details or {}
		if candidate_details.get('superseded'):
			continue

		if candidate.created_at >= reuse_cutoff and candidate.payment_session_id:
			return Response({
				'success': True,
				'data': {
					'order_id': candidate.order_id,
					'cf_order_id': candidate.cf_order_id,
					'payment_session_id': candidate.payment_session_id,
					'plan_code': plan.code,
					'amount': str(candidate.amount),
					'action': quote['action'],
					'summary': quote.get('summary', ''),
					'currency': candidate.currency,
					'reused_order': True,
				}
			})

		candidate_details['superseded'] = True
		candidate_details['superseded_reason'] = 'newer_payment_intent_created'
		candidate_details['superseded_at'] = now.isoformat()
		candidate.billing_details = candidate_details
		candidate.save(update_fields=['billing_details', 'updated_at'])

	order_id = f"sub_{str(tenant.id).replace('-', '')[:8]}_{uuid.uuid4().hex[:18]}"
	request_id = str(uuid.uuid4())
	idempotency_key = str(uuid.uuid4())

	body = {
		'order_id': order_id,
		'order_currency': 'INR',
		'order_amount': float(order_amount),
		'customer_details': {
			'customer_id': str(tenant.id),
			'customer_name': tenant.business_name or tenant.username or 'Cenvora User',
			'customer_email': tenant.email or 'support@cenvora.app',
			'customer_phone': tenant.phone or '9999999999',
		},
		'order_note': quote.get('summary') or f'Plan payment for {plan.name}',
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
		amount=order_amount,
		currency='INR',
		status=SubscriptionPaymentStatus.PENDING,
		action=quote['action'],
		source_plan_code=quote.get('source_plan_code'),
		billing_details={
			'summary': quote.get('summary', ''),
			'apply_immediately': quote.get('apply_immediately', False),
			'quoted_amount': str(order_amount),
			'intent_key': f"{quote['action']}:{quote.get('source_plan_code') or 'free'}->{plan.code}:{order_amount}",
			'superseded': False,
		},
		raw_response=response_data,
	)

	return Response({
		'success': True,
		'data': {
			'order_id': order_id,
			'cf_order_id': response_data.get('cf_order_id'),
			'payment_session_id': response_data.get('payment_session_id'),
			'plan_code': plan.code,
			'amount': str(order_amount),
			'action': quote['action'],
			'summary': quote.get('summary', ''),
			'currency': 'INR',
			'reused_order': False,
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

	billing_details = payment.billing_details or {}
	if billing_details.get('superseded'):
		return Response({
			'success': False,
			'error': 'This payment order is no longer active. Please use the latest payment request.',
			'data': {
				'order_id': order_id,
				'status': payment.status,
				'reason': 'superseded_order',
			}
		}, status=status.HTTP_409_CONFLICT)

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
	payment_action = payment.action or SubscriptionPaymentAction.ACTIVATE

	if payment_action == SubscriptionPaymentAction.UPGRADE_NOW and active_until:
		# Instant upgrade: switch plan now, keep current cycle end.
		subscription.plan = payment.plan
		subscription.status = SubscriptionStatus.ACTIVE
		subscription.cancel_at_period_end = False
		subscription.pending_plan = None
		subscription.pending_plan_starts_at = None
		subscription.save(update_fields=[
			'plan', 'status', 'cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'
		])
		_sync_legacy_subscription_fields(tenant, payment.plan.code)
	elif payment_action == SubscriptionPaymentAction.RENEW and active_until:
		subscription.current_period_end = active_until + timedelta(days=30)
		subscription.status = SubscriptionStatus.ACTIVE
		subscription.cancel_at_period_end = False
		subscription.pending_plan = None
		subscription.pending_plan_starts_at = None
		subscription.save(update_fields=[
			'current_period_end', 'status', 'cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'
		])
		_sync_legacy_subscription_fields(tenant, subscription.plan.code)
	elif active_until and subscription.plan_id != payment.plan_id:
		# Backward-compatible fallback: queue when a different-plan payment appears without explicit action.
		subscription.pending_plan = payment.plan
		subscription.pending_plan_starts_at = active_until
		subscription.status = SubscriptionStatus.ACTIVE
		subscription.cancel_at_period_end = False
		subscription.save(update_fields=['pending_plan', 'pending_plan_starts_at', 'status', 'cancel_at_period_end', 'updated_at'])
		queued = True
		queued_starts_at = active_until
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
			'action': payment_action,
			'queued_plan_code': subscription.pending_plan.code if subscription.pending_plan else None,
			'queued': queued,
			'queued_starts_at': queued_starts_at,
			'subscription_status': subscription.status,
			'current_period_end': subscription.current_period_end,
		}
	})


@csrf_exempt
@api_view(['POST'])
@permission_classes([])
def cashfree_webhook(request):
	"""
	Webhook endpoint for Cashfree payment gateway.
	Receives and verifies payment events, then queues async processing.
	
	Expected headers:
	- x-webhook-signature: HMAC-SHA256 signature of payload
	- x-idempotency-key: unique event key
	- x-webhook-timestamp: milliseconds epoch
	
	Expected payload:
	{
		"event": "PAYMENT_SUCCESS",
		"eventId": "unique_event_id_from_cashfree",
		"data": {
			"orderId": "order_id_in_cenvora",
			"paymentId": "cashfree_payment_id",
			...
		}
	}
	"""
	try:
		# Get raw payload for signature verification
		payload_str = request.body.decode('utf-8')
		payload = request.data or json.loads(payload_str)
	except Exception as e:
		logger.error(f"Error parsing webhook payload: {e}")
		return Response({'error': 'Invalid payload'}, status=status.HTTP_400_BAD_REQUEST)
	
	# Extract webhook details
	event_type = _normalize_webhook_event_type(payload.get('event') or payload.get('type') or payload.get('event_type') or '')
	header_idempotency_key = str(request.headers.get('x-idempotency-key', '')).strip()
	payload_event_id = payload.get('eventId') or payload.get('event_id')
	event_id = header_idempotency_key or (str(payload_event_id).strip() if payload_event_id else '')
	if not event_id:
		# Deterministic fallback keeps retries idempotent even without explicit key.
		event_id = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

	order_id = _extract_order_id(payload)
	
	if not order_id:
		logger.warning(f"Webhook {event_id} missing orderId")
		return Response({'error': 'orderId is required'}, status=status.HTTP_400_BAD_REQUEST)
	
	# Verify signature
	signature = request.headers.get('x-webhook-signature', '') or request.headers.get('x-cashfree-signature', '')
	timestamp = str(request.headers.get('x-webhook-timestamp', '')).strip()
	webhook_secret = getattr(settings, 'CASHFREE_WEBHOOK_SECRET', '')
	require_signature_setting = bool(getattr(settings, 'CASHFREE_REQUIRE_WEBHOOK_SIGNATURE', True))
	allow_unsigned = bool(getattr(settings, 'CASHFREE_ALLOW_UNSIGNED_WEBHOOKS', False))
	require_signature = require_signature_setting or bool(webhook_secret) or not allow_unsigned

	if require_signature and not signature:
		logger.error(f"Webhook {event_id} missing signature header")
		return Response({'error': 'Signature is required'}, status=status.HTTP_401_UNAUTHORIZED)

	if require_signature and not timestamp:
		logger.error(f"Webhook {event_id} missing timestamp header")
		return Response({'error': 'Webhook timestamp is required'}, status=status.HTTP_401_UNAUTHORIZED)

	max_skew_ms = int(getattr(settings, 'CASHFREE_WEBHOOK_MAX_SKEW_MS', 10 * 60 * 1000))
	if require_signature:
		try:
			request_ts = int(timestamp)
			now_ts = int(timezone.now().timestamp() * 1000)
			if abs(now_ts - request_ts) > max_skew_ms:
				logger.error(f"Webhook {event_id} timestamp outside allowed skew")
				return Response({'error': 'Webhook timestamp expired'}, status=status.HTTP_401_UNAUTHORIZED)
		except ValueError:
			logger.error(f"Webhook {event_id} has invalid timestamp header")
			return Response({'error': 'Invalid webhook timestamp'}, status=status.HTTP_400_BAD_REQUEST)

	if require_signature:
		if not verify_cashfree_signature(payload_str, signature, timestamp=timestamp or None):
			logger.error(f"Webhook {event_id} signature verification failed")
			return Response({'error': 'Signature verification failed'}, status=status.HTTP_401_UNAUTHORIZED)
	else:
		logger.warning(
			"Accepting unsigned webhook event %s because CASHFREE_ALLOW_UNSIGNED_WEBHOOKS is enabled.",
			event_id,
		)
	
	# Check for duplicate (idempotency)
	if WebhookEvent.objects.filter(event_id=event_id).exists():
		logger.info(f"Webhook {event_id} already exists, returning 200 OK")
		return Response({'status': 'received'}, status=status.HTTP_200_OK)
	
	# Queue async processing with Celery
	try:
		process_cashfree_webhook.delay(
			event_id=event_id,
			event_type=event_type,
			order_id=order_id,
			payload=payload,
		)
		logger.info(f"Queued webhook {event_id} ({event_type}) for async processing")
	except Exception as e:
		logger.error(f"Error queuing webhook {event_id}: {e}")
		# Still return 200 OK to Cashfree so it doesn't retry infinitely
		# The event is recorded in WebhookEvent for manual review
	
	return Response({'status': 'received'}, status=status.HTTP_200_OK)

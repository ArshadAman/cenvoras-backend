from datetime import datetime, timedelta
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import BillingCycle, Plan, SubscriptionPaymentOrder, SubscriptionStatus, TenantSubscription
from .services import get_entitlements, get_tenant


def _normalize_cycle(cycle: str | None) -> str:
	normalized = str(cycle or BillingCycle.MONTHLY).lower()
	if normalized in {BillingCycle.MONTHLY, BillingCycle.QUARTERLY, BillingCycle.YEARLY}:
		return normalized
	return BillingCycle.MONTHLY


def _days_for_cycle(cycle: str) -> int:
	normalized = _normalize_cycle(cycle)
	if normalized == BillingCycle.YEARLY:
		return 365
	if normalized == BillingCycle.QUARTERLY:
		return 90
	return 30


def _get_plan_change_data(subscription, target_plan, target_cycle):
	now = timezone.now()
	current_plan = subscription.plan
	current_code = str(current_plan.code or 'free').lower() if current_plan else 'free'
	target_code = str(target_plan.code or 'free').lower()
	current_rank = _rank(current_code)
	target_rank = _rank(target_code)
	current_is_free = current_code in {'free', 'starter'}

	action = 'upgrade'
	payment_required = True
	effective_at = now

	if target_plan.is_free:
		action = 'current' if target_code == current_code else 'downgrade_not_allowed'
		payment_required = False
		effective_at = subscription.current_period_end or now
	elif target_code == current_code and target_cycle == _normalize_cycle(subscription.current_billing_cycle):
		action = 'renewal'
	elif target_code == current_code:
		action = 'upgrade'
	elif target_rank < current_rank and target_plan.is_free:
		action = 'downgrade_not_allowed'
		payment_required = False
		effective_at = subscription.current_period_end or now
	elif target_rank < current_rank:
		action = 'unsupported_paid_schedule'
		payment_required = False
		effective_at = subscription.current_period_end or now

	credit = Decimal('0.00')
	days_used = 0
	days_remaining = 0
	current_daily_rate = Decimal('0.00')
	current_cycle_price = Decimal('0.00')
	current_cycle_total_days = 0
	new_plan_full_price = Decimal('0.00')
	amount = Decimal('0.00')

	if payment_required:
		new_plan_full_price = target_plan.price_for_cycle(target_cycle)
		
		has_active_paid_period = (
			not current_is_free
			and subscription.current_period_end
			and subscription.current_period_end > now
			and subscription.current_period_start
		)

		if has_active_paid_period and action != 'renewal':
			current_cycle = _normalize_cycle(subscription.current_billing_cycle)
			current_cycle_total_days = _days_for_cycle(current_cycle)
			current_cycle_price = current_plan.price_for_cycle(current_cycle)

			days_used = max(0, (now - subscription.current_period_start).days)
			days_remaining = max(0, current_cycle_total_days - days_used)

			if current_cycle_total_days > 0 and days_remaining > 0:
				current_daily_rate = current_cycle_price / Decimal(str(current_cycle_total_days))
				credit = (current_daily_rate * Decimal(str(days_remaining))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

			amount = max(Decimal('0.00'), new_plan_full_price - credit)
			effective_at = now
		elif action == 'renewal':
			amount = new_plan_full_price
			effective_at = subscription.current_period_end or now
		else:
			amount = new_plan_full_price

		if amount > Decimal('0.00') and amount < Decimal('1.00'):
			amount = Decimal('1.00')

	base_price_before_discount = target_plan.original_price_for_cycle(target_cycle)

	summary = ""
	if payment_required:
		if credit > Decimal('0.00'):
			summary = f"Upgrade to {target_plan.name}. Credit of INR {credit} for {days_remaining} unused days applied. Pay INR {amount}."
		else:
			summary = f"Upgrade to {target_plan.name}. Pay INR {amount}."

	return {
		'action': action,
		'payment_required': payment_required,
		'amount': amount,
		'new_plan_full_price': new_plan_full_price,
		'base_price_before_discount': base_price_before_discount,
		'credit': credit,
		'days_used': days_used,
		'days_remaining': days_remaining,
		'current_daily_rate': current_daily_rate,
		'current_cycle_price': current_cycle_price,
		'current_cycle_total_days': current_cycle_total_days,
		'effective_at': effective_at,
		'summary': summary,
	}


def _rank(plan_code: str | None) -> int:
	code = str(plan_code or '').lower()
	if code == 'business':
		return 2
	if code == 'pro':
		return 1
	return 0


def _add_days(dt: datetime, days: int) -> datetime:
	return dt + timedelta(days=int(days))


def _coerce_bool(value) -> bool:
	if isinstance(value, bool):
		return value
	return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_subscription(tenant):
	subscription, _ = TenantSubscription.objects.get_or_create(
		tenant=tenant,
		defaults={
			'plan': Plan.objects.filter(code='free').first() or Plan.objects.filter(is_active=True).order_by('monthly_price').first(),
			'status': SubscriptionStatus.TRIAL if getattr(tenant, 'is_trial_active', False) else SubscriptionStatus.ACTIVE,
			'current_period_start': timezone.now(),
			'current_billing_cycle': BillingCycle.MONTHLY,
		},
	)
	return subscription


def _apply_pending_if_due(subscription: TenantSubscription):
	if not subscription.pending_plan or not subscription.pending_plan_starts_at:
		return
	now = timezone.now()
	if now < subscription.pending_plan_starts_at:
		return

	cycle = _normalize_cycle(subscription.pending_billing_cycle)
	start_at = subscription.pending_plan_starts_at
	end_at = _add_days(start_at, _days_for_cycle(cycle))
	subscription.plan = subscription.pending_plan
	subscription.current_billing_cycle = cycle
	subscription.current_period_start = start_at
	subscription.current_period_end = end_at
	subscription.status = SubscriptionStatus.ACTIVE
	subscription.pending_plan = None
	subscription.pending_billing_cycle = None
	subscription.pending_plan_starts_at = None
	subscription.save(update_fields=[
		'plan',
		'current_billing_cycle',
		'current_period_start',
		'current_period_end',
		'status',
		'pending_plan',
		'pending_billing_cycle',
		'pending_plan_starts_at',
		'updated_at',
	])


def _serialize_order(order: SubscriptionPaymentOrder):
	return {
		'order_id': order.order_id,
		'payment_session_id': order.payment_session_id,
		'status': order.status,
		'amount': str(order.amount),
		'plan_code': order.target_plan.code,
		'plan_name': order.target_plan.name,
		'billing_cycle': order.billing_cycle,
		'duration_days': order.duration_days,
		'created_at': order.created_at,
		'paid_at': order.paid_at,
		'failure_reason': order.failure_reason,
		'cashfree_env': 'sandbox',
		# Frontend uses this to skip checkout when backend is not yet wired to a payment gateway.
		'skip_checkout': not bool(order.payment_session_id),
	}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_entitlements(request):
	tenant = get_tenant(request.user)
	subscription = _get_subscription(tenant)
	_apply_pending_if_due(subscription)
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
			'monthly_price': str(plan.price_for_cycle(BillingCycle.MONTHLY)),
			'quarterly_price': str(plan.price_for_cycle(BillingCycle.QUARTERLY)),
			'yearly_price': str(plan.price_for_cycle(BillingCycle.YEARLY)),
			'original_monthly_price': str(plan.original_price_for_cycle(BillingCycle.MONTHLY)),
			'original_quarterly_price': str(plan.original_price_for_cycle(BillingCycle.QUARTERLY)),
			'original_yearly_price': str(plan.original_price_for_cycle(BillingCycle.YEARLY)),
			'max_managers': plan.max_managers,
			'max_team_members': getattr(plan, 'max_team_members', plan.max_managers),
			'max_customers': getattr(plan, 'max_customers', -1),
			'max_invoices_per_month': plan.max_invoices_per_month,
			'features': [feature.code for feature in plan.features.all()],
			'cycle_days': {
				BillingCycle.MONTHLY: _days_for_cycle(BillingCycle.MONTHLY),
				BillingCycle.QUARTERLY: _days_for_cycle(BillingCycle.QUARTERLY),
				BillingCycle.YEARLY: _days_for_cycle(BillingCycle.YEARLY),
			},
		})

	return Response({
		'success': True,
		'data': data,
	})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_change_quote(request):
	target_plan_code = str(request.data.get('target_plan_code') or '').lower().strip()
	billing_cycle = _normalize_cycle(request.data.get('billing_cycle'))
	if not target_plan_code:
		return Response({'success': False, 'error': 'target_plan_code is required.'}, status=status.HTTP_400_BAD_REQUEST)

	target_plan = Plan.objects.filter(code=target_plan_code, is_active=True).first()
	if not target_plan:
		return Response({'success': False, 'error': 'Requested plan does not exist.'}, status=status.HTTP_404_NOT_FOUND)

	tenant = get_tenant(request.user)
	subscription = _get_subscription(tenant)
	_apply_pending_if_due(subscription)
	
	quote_data = _get_plan_change_data(subscription, target_plan, billing_cycle)
	
	summary = ""
	if quote_data['payment_required']:
		cycle_label = billing_cycle.capitalize()
		if quote_data['credit'] > Decimal('0.00'):
			summary = (
				f"Upgrade to {target_plan.name} ({cycle_label}). "
				f"Credit of INR {quote_data['credit']} applied for unused days. "
				f"Total to pay: INR {quote_data['amount']}."
			)
		else:
			summary = f"Upgrade to {target_plan.name} ({cycle_label}). Total: INR {quote_data['amount']}."
	else:
		summary = f"{target_plan.name} — no payment required."

	return Response({
		'success': True,
		'data': {
			'action': quote_data['action'],
			'payment_required': quote_data['payment_required'],
			'target_plan_code': target_plan.code,
			'target_plan_name': target_plan.name,
			'billing_cycle': billing_cycle,
			'amount': str(quote_data['amount']),
			'original_amount': str(quote_data['base_price_before_discount']),
			'base_price_before_discount': str(quote_data['base_price_before_discount']),
			'new_plan_full_price': str(quote_data['new_plan_full_price']),
			'credit': str(quote_data['credit']),
			'days_used': quote_data['days_used'],
			'days_remaining': quote_data['days_remaining'],
			'current_daily_rate': str(quote_data['current_daily_rate'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
			'current_cycle_price': str(quote_data['current_cycle_price']),
			'current_cycle_total_days': quote_data['current_cycle_total_days'],
			'duration_days': _days_for_cycle(billing_cycle) if quote_data['payment_required'] else 0,
			'effective_at': quote_data['effective_at'],
			'summary': summary,
		},
	})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def schedule_plan_change(request):
	target_plan_code = str(request.data.get('target_plan_code') or '').lower().strip()
	billing_cycle = _normalize_cycle(request.data.get('billing_cycle'))
	if not target_plan_code:
		return Response({'success': False, 'error': 'target_plan_code is required.'}, status=status.HTTP_400_BAD_REQUEST)

	target_plan = Plan.objects.filter(code=target_plan_code, is_active=True).first()
	if not target_plan:
		return Response({'success': False, 'error': 'Requested plan does not exist.'}, status=status.HTTP_404_NOT_FOUND)

	tenant = get_tenant(request.user)
	subscription = _get_subscription(tenant)
	_apply_pending_if_due(subscription)
	if not subscription.current_period_end:
		return Response({'success': False, 'error': 'No active paid period found to schedule against.'}, status=status.HTTP_400_BAD_REQUEST)

	subscription.pending_plan = target_plan
	subscription.pending_billing_cycle = billing_cycle
	subscription.pending_plan_starts_at = subscription.current_period_end
	subscription.save(update_fields=['pending_plan', 'pending_billing_cycle', 'pending_plan_starts_at', 'updated_at'])

	return Response({
		'success': True,
		'data': {
			'message': f'Next plan ({target_plan.name}, {billing_cycle}) is scheduled.',
			'effective_at': subscription.pending_plan_starts_at,
		},
	})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_plan_payment_order(request):
	plan_code = str(request.data.get('plan_code') or '').lower().strip()
	billing_cycle = _normalize_cycle(request.data.get('billing_cycle'))
	force_new_order = _coerce_bool(request.data.get('force_new_order'))
	if not plan_code:
		return Response({'success': False, 'error': 'plan_code is required.'}, status=status.HTTP_400_BAD_REQUEST)

	target_plan = Plan.objects.filter(code=plan_code, is_active=True).first()
	if not target_plan:
		return Response({'success': False, 'error': 'Requested plan does not exist.'}, status=status.HTTP_404_NOT_FOUND)

	tenant = get_tenant(request.user)
	subscription = _get_subscription(tenant)
	_apply_pending_if_due(subscription)
	
	quote_data = _get_plan_change_data(subscription, target_plan, billing_cycle)
	amount = quote_data['amount']
	duration_days = _days_for_cycle(billing_cycle)

	if target_plan.is_free or (amount <= Decimal('0.00') and quote_data['action'] != 'upgrade'):
		return Response({'success': False, 'error': 'No payment is required for this plan.'}, status=status.HTTP_400_BAD_REQUEST)

	if not force_new_order:
		existing = SubscriptionPaymentOrder.objects.filter(
			tenant=tenant,
			target_plan=target_plan,
			billing_cycle=billing_cycle,
			amount=amount,
			duration_days=duration_days,
			status=SubscriptionPaymentOrder.OrderStatus.CREATED,
		).first()
		if existing:
			return Response({'success': True, 'data': _serialize_order(existing)})

	order = SubscriptionPaymentOrder.objects.create(
		tenant=tenant,
		order_id=f"SUB-{uuid.uuid4().hex[:16].upper()}",
		payment_session_id='',
		target_plan=target_plan,
		billing_cycle=billing_cycle,
		duration_days=duration_days,
		amount=amount,
		status=SubscriptionPaymentOrder.OrderStatus.CREATED,
	)

	return Response({'success': True, 'data': _serialize_order(order)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def confirm_plan_payment(request):
	order_id = str(request.data.get('order_id') or '').strip()
	if not order_id:
		return Response({'success': False, 'error': 'order_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

	tenant = get_tenant(request.user)
	order = SubscriptionPaymentOrder.objects.select_for_update().filter(order_id=order_id, tenant=tenant).first()
	if not order:
		return Response({'success': False, 'error': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

	if order.status == SubscriptionPaymentOrder.OrderStatus.SUCCESS:
		return Response({'success': True, 'data': _serialize_order(order)})

	now = timezone.now()
	order.status = SubscriptionPaymentOrder.OrderStatus.SUCCESS
	order.paid_at = now
	order.failure_reason = ''
	order.save(update_fields=['status', 'paid_at', 'failure_reason', 'updated_at'])

	subscription = _get_subscription(tenant)
	_apply_pending_if_due(subscription)

	# Calculate new period
	quote_data = _get_plan_change_data(subscription, order.target_plan, order.billing_cycle)
	
	if quote_data['action'] == 'upgrade' or quote_data['credit'] > Decimal('0.00'):
		# For upgrades with credit or tier change, start from now
		base = now
	else:
		# For renewals or other cases, append to end if current period is valid
		base = subscription.current_period_end if subscription.current_period_end and subscription.current_period_end > now else now
	
	duration_days = order.duration_days or _days_for_cycle(order.billing_cycle)
	new_end = _add_days(base, duration_days)

	subscription.plan = order.target_plan
	subscription.current_billing_cycle = order.billing_cycle
	subscription.current_period_start = base
	subscription.current_period_end = new_end
	subscription.status = SubscriptionStatus.ACTIVE
	subscription.save(update_fields=[
		'plan',
		'current_billing_cycle',
		'current_period_start',
		'current_period_end',
		'status',
		'updated_at',
	])

	tenant_update_fields = []
	if hasattr(tenant, 'subscription_status'):
		tenant.subscription_status = 'active'
		tenant_update_fields.append('subscription_status')
	if hasattr(tenant, 'subscription_tier'):
		tenant.subscription_tier = 'PRO' if order.target_plan.is_business else 'MID'
		tenant_update_fields.append('subscription_tier')
	if tenant_update_fields:
		tenant.save(update_fields=tenant_update_fields)

	return Response({'success': True, 'data': _serialize_order(order)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def latest_payment_status(request):
	tenant = get_tenant(request.user)
	latest = SubscriptionPaymentOrder.objects.filter(tenant=tenant).first()
	if not latest:
		return Response({'success': True, 'data': None})
	return Response({'success': True, 'data': _serialize_order(latest)})

"""
Subscription Payment Processing Tasks — Async Celery tasks for webhook handling,
payment confirmation, and transactional email notifications.
"""
import logging
import base64
import hashlib
import hmac
from datetime import timedelta
import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import (
    SubscriptionPayment,
    SubscriptionPaymentStatus,
    SubscriptionPaymentAction,
    SubscriptionStatus,
    TenantSubscription,
    WebhookEvent,
)
from integration.tasks import send_async_email_notification

User = get_user_model()
logger = logging.getLogger(__name__)


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


def _fetch_success_payment_attempt(order_id: str):
    if not settings.CASHFREE_CLIENT_ID or not settings.CASHFREE_CLIENT_SECRET:
        logger.error("Cashfree credentials are missing; cannot verify webhook payment status.")
        return None

    response = requests.get(
        f"{_cashfree_base_url()}/orders/{order_id}/payments",
        headers=_cashfree_headers(),
        timeout=20,
    )

    if response.status_code >= 400:
        logger.error("Cashfree verification API failed for order %s with status %s", order_id, response.status_code)
        return None

    try:
        data = response.json()
    except Exception:
        logger.error("Cashfree verification API returned non-JSON response for order %s", order_id)
        return None

    attempts = data.get('data', []) if isinstance(data, dict) else data
    if not isinstance(attempts, list):
        return None

    for attempt in attempts:
        if attempt.get('payment_status') == 'SUCCESS':
            return attempt

    return None


def _sync_legacy_subscription_fields(tenant, plan_code):
    tier_map = {
        'free': 'FREE',
        'pro': 'MID',
        'business': 'PRO',
    }
    tenant.subscription_status = 'active'
    tenant.subscription_tier = tier_map.get(plan_code, 'FREE')
    tenant.save(update_fields=['subscription_status', 'subscription_tier'])


def verify_cashfree_signature(payload_str: str, signature: str, timestamp: str | None = None) -> bool:
    """
    Verify Cashfree webhook signature using HMAC-SHA256.
    Supports both payload-only and timestamp.payload signing modes.
    Secret resolution matches Cashfree SDK style: dedicated webhook secret,
    falling back to Cashfree PG client secret when webhook secret is not set.
    """
    webhook_secret = getattr(settings, 'CASHFREE_WEBHOOK_SECRET', '')
    require_signature = bool(getattr(settings, 'CASHFREE_REQUIRE_WEBHOOK_SIGNATURE', True))
    allow_unsigned = bool(getattr(settings, 'CASHFREE_ALLOW_UNSIGNED_WEBHOOKS', False))

    if not webhook_secret:
        if allow_unsigned and not require_signature:
            logger.warning("CASHFREE_WEBHOOK_SECRET not configured. Unsigned webhooks allowed by configuration.")
            return True
        logger.error("CASHFREE_WEBHOOK_SECRET not configured while signature verification is required.")
        return False
    
    try:
        signatures_to_check = []

        # Mode 1: signature over raw payload
        payload_hmac = hmac.new(
            webhook_secret.encode(),
            payload_str.encode(),
            hashlib.sha256,
        ).digest()
        signatures_to_check.append(base64.b64encode(payload_hmac).decode())

        # Mode 2: signature over "timestamp.payload"
        if timestamp:
            signed_content = f"{timestamp}.{payload_str}"
            ts_hmac = hmac.new(
                webhook_secret.encode(),
                signed_content.encode(),
                hashlib.sha256,
            ).digest()
            signatures_to_check.append(base64.b64encode(ts_hmac).decode())

        return any(hmac.compare_digest(candidate, signature) for candidate in signatures_to_check)
    except Exception as e:
        logger.error(f"Error verifying Cashfree signature: {e}")
        return False


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def process_cashfree_webhook(self, event_id: str, event_type: str, order_id: str, payload: dict):
    """
    Process Cashfree webhook asynchronously via Celery.
    Handles payment success/failure events and updates subscription state.
    """
    logger.info(f"Processing webhook: {event_type} for order {order_id} (event_id: {event_id})")
    
    # Prevent duplicate processing using event_id
    webhook_event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            'event_type': event_type,
            'order_id': order_id,
            'payload': payload,
            'provider': 'cashfree',
        }
    )
    
    if webhook_event.processed:
        logger.info(f"Webhook {event_id} already processed. Skipping.")
        return {'status': 'skipped', 'reason': 'already_processed'}
    
    try:
        event_payload = payload.get('data', payload) if isinstance(payload, dict) else {}

        if event_type == 'PAYMENT_SUCCESS':
            result = _handle_payment_success(webhook_event, order_id, event_payload)
        elif event_type == 'PAYMENT_FAILED':
            result = _handle_payment_failed(webhook_event, order_id, event_payload)
        elif event_type == 'PAYMENT_PENDING':
            result = _handle_payment_pending(webhook_event, order_id, event_payload)
        else:
            logger.warning(f"Unknown event type: {event_type}")
            result = {'status': 'ignored', 'reason': 'unknown_event_type'}
        
        webhook_event.processed = True
        webhook_event.processed_at = timezone.now()
        webhook_event.save()
        
        logger.info(f"Webhook {event_id} processed successfully: {result}")
        return result
    
    except Exception as e:
        logger.error(f"Error processing webhook {event_id}: {e}", exc_info=True)
        webhook_event.processed = False
        webhook_event.error_message = str(e)
        webhook_event.save()
        raise


def _handle_payment_success(webhook_event, order_id: str, payload: dict):
    """Handle successful payment webhook."""
    try:
        payment = SubscriptionPayment.objects.select_related('plan', 'tenant').get(
            order_id=order_id
        )
    except SubscriptionPayment.DoesNotExist:
        logger.warning(f"Payment order {order_id} not found in webhook")
        return {'status': 'error', 'reason': 'payment_not_found'}

    billing_details = payment.billing_details or {}
    if billing_details.get('superseded'):
        now = timezone.now()
        payment.status = SubscriptionPaymentStatus.SUCCESS
        payment.cf_payment_id = payload.get('payment_id') or payload.get('cf_payment_id')
        payment.paid_at = now
        payment.raw_response = {
            'webhook_payload': payload,
            'note': 'superseded_order_paid_no_subscription_change',
        }
        payment.save(update_fields=['status', 'cf_payment_id', 'paid_at', 'raw_response', 'updated_at'])

        if payment.tenant.email:
            send_async_email_notification.delay(
                user_id=payment.tenant.id,
                to_email=payment.tenant.email,
                subject="Payment Received For Old Order - Cenvora",
                body=f"""
Hello {payment.tenant.first_name or payment.tenant.username},

We received a payment for an older/superseded order ({order_id}).

This payment was not applied to change your subscription because a newer payment intent exists.
Please contact support@cenvora.app so we can help with refund/reconciliation.

Best regards,
Cenvora Team
""",
                related_model='SubscriptionPayment',
                related_id=order_id,
            )

        return {'status': 'ignored', 'reason': 'superseded_order_paid'}
    
    verified_success_attempt = _fetch_success_payment_attempt(order_id)
    if not verified_success_attempt:
        logger.warning("Webhook marked success but Cashfree verification found no SUCCESS payment for order %s", order_id)
        payment.raw_response = {
            'webhook_payload': payload,
            'verification': 'no_success_attempt',
        }
        payment.save(update_fields=['raw_response', 'updated_at'])
        return {'status': 'ignored', 'reason': 'unverified_success'}

    tenant = payment.tenant
    subscription, _created = TenantSubscription.objects.get_or_create(
        tenant=tenant,
        defaults={
            'plan': payment.plan,
            'status': SubscriptionStatus.ACTIVE,
            'current_period_start': timezone.now(),
            'current_period_end': timezone.now() + timedelta(days=30),
            'cancel_at_period_end': False,
        }
    )
    
    now = timezone.now()
    payment.status = SubscriptionPaymentStatus.SUCCESS
    payment.cf_payment_id = (
        verified_success_attempt.get('cf_payment_id')
        or verified_success_attempt.get('payment_id')
        or payload.get('payment_id')
        or payload.get('cf_payment_id')
    )
    payment.paid_at = now
    payment.raw_response = {
        'webhook_payload': payload,
        'verified_success_attempt': verified_success_attempt,
    }
    payment.save(update_fields=['status', 'cf_payment_id', 'paid_at', 'raw_response', 'updated_at'])
    
    # Apply payment action
    active_until = subscription.current_period_end if subscription.current_period_end and subscription.current_period_end > now else None
    payment_action = payment.action or SubscriptionPaymentAction.ACTIVATE
    
    if payment_action == SubscriptionPaymentAction.UPGRADE_NOW and active_until:
        # Instant upgrade: switch plan now, keep cycle end
        subscription.plan = payment.plan
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.cancel_at_period_end = False
        subscription.pending_plan = None
        subscription.pending_plan_starts_at = None
        subscription.save(update_fields=[
            'plan', 'status', 'cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'
        ])
        _sync_legacy_subscription_fields(tenant, payment.plan.code)
        action_summary = f"Upgraded to {payment.plan.name}"
    
    elif payment_action == SubscriptionPaymentAction.RENEW and active_until:
        # Renewal: extend current cycle
        subscription.current_period_end = active_until + timedelta(days=30)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.cancel_at_period_end = False
        subscription.pending_plan = None
        subscription.pending_plan_starts_at = None
        subscription.save(update_fields=[
            'current_period_end', 'status', 'cancel_at_period_end', 'pending_plan', 'pending_plan_starts_at', 'updated_at'
        ])
        _sync_legacy_subscription_fields(tenant, subscription.plan.code)
        action_summary = f"Renewed {subscription.plan.name} plan"
    
    else:
        # Activate new plan for 30 days (no active cycle or initial activation)
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
        action_summary = f"Activated {payment.plan.name} plan"
    
    # Send confirmation email asynchronously
    send_payment_confirmation_email.delay(
        user_id=tenant.id,
        plan_name=payment.plan.name,
        amount=str(payment.amount),
        action=action_summary,
        order_id=order_id,
    )
    
    return {
        'status': 'success',
        'action': action_summary,
        'plan': payment.plan.code,
        'subscription_id': subscription.id,
    }


def _handle_payment_failed(webhook_event, order_id: str, payload: dict):
    """Handle failed payment webhook."""
    try:
        payment = SubscriptionPayment.objects.get(order_id=order_id)
    except SubscriptionPayment.DoesNotExist:
        logger.warning(f"Payment order {order_id} not found in webhook")
        return {'status': 'error', 'reason': 'payment_not_found'}
    
    payment.status = SubscriptionPaymentStatus.FAILED
    payment.raw_response = payload
    payment.save(update_fields=['status', 'raw_response', 'updated_at'])
    
    # Send failure email
    send_payment_failure_email.delay(
        user_id=payment.tenant.id,
        order_id=order_id,
        reason=payload.get('error_message', 'Payment processing failed'),
    )
    
    return {
        'status': 'failed',
        'order_id': order_id,
        'reason': payload.get('error_message'),
    }


def _handle_payment_pending(webhook_event, order_id: str, payload: dict):
    """Handle pending payment webhook."""
    try:
        payment = SubscriptionPayment.objects.get(order_id=order_id)
    except SubscriptionPayment.DoesNotExist:
        return {'status': 'error', 'reason': 'payment_not_found'}
    
    payment.status = SubscriptionPaymentStatus.PENDING
    payment.raw_response = payload
    payment.save(update_fields=['status', 'raw_response', 'updated_at'])
    
    return {'status': 'pending', 'order_id': order_id}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def send_payment_confirmation_email(self, user_id: int, plan_name: str, amount: str, action: str, order_id: str):
    """
    Send payment confirmation email from Cenvora.
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for payment confirmation email")
        return {'status': 'failed', 'reason': 'user_not_found'}
    
    subject = "Payment Confirmation - Cenvora"
    body = f"""
Hello {user.first_name or user.username},

Thank you for your payment! Your subscription has been successfully processed.

Plan: {plan_name}
Amount: ₹{amount}
Action: {action}
Order ID: {order_id}

Your plan is now active and you have full access to all features.

If you have any questions, please contact our support team.

Best regards,
Cenvora Team
support@cenvora.app
"""
    
    send_async_email_notification.delay(
        user_id=user_id,
        to_email=user.email,
        subject=subject,
        body=body,
        related_model='SubscriptionPayment',
        related_id=order_id,
    )
    
    logger.info(f"Queued confirmation email for user {user_id} (order {order_id})")
    return {'status': 'queued', 'user_id': user_id}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def send_payment_failure_email(self, user_id: int, order_id: str, reason: str):
    """
    Send payment failure notification email from Cenvora.
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for payment failure email")
        return {'status': 'failed', 'reason': 'user_not_found'}
    
    subject = "Payment Failed - Cenvora"
    body = f"""
Hello {user.first_name or user.username},

Unfortunately, your payment could not be processed.

Order ID: {order_id}
Reason: {reason}

Please try again or contact our support team for assistance.

To retry your payment, visit: https://cenvora.app/profile

Best regards,
Cenvora Team
support@cenvora.app
"""
    
    send_async_email_notification.delay(
        user_id=user_id,
        to_email=user.email,
        subject=subject,
        body=body,
        related_model='SubscriptionPayment',
        related_id=order_id,
    )
    
    logger.info(f"Queued failure email for user {user_id} (order {order_id})")
    return {'status': 'queued', 'user_id': user_id}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=2)
def auto_activate_pending_plans():
    """
    Scheduled task to auto-activate pending plans when their start date arrives.
    Run this via Celery Beat every hour.
    """
    now = timezone.now()
    activated = 0
    
    # Find subscriptions with pending plans that should activate now
    subscriptions = TenantSubscription.objects.filter(
        pending_plan__isnull=False,
        pending_plan_starts_at__lte=now,
        status=SubscriptionStatus.ACTIVE,
    )
    
    for subscription in subscriptions:
        try:
            subscription.plan = subscription.pending_plan
            subscription.pending_plan = None
            subscription.pending_plan_starts_at = None
            subscription.save(update_fields=['plan', 'pending_plan', 'pending_plan_starts_at', 'updated_at'])
            
            _sync_legacy_subscription_fields(subscription.tenant, subscription.plan.code)
            
            # Send notification email
            send_async_email_notification.delay(
                user_id=subscription.tenant.id,
                to_email=subscription.tenant.email,
                subject="Plan Changed - Cenvora",
                body=f"""
Hello {subscription.tenant.first_name or subscription.tenant.username},

Your plan has been changed to {subscription.plan.name}.

The change is now effective.

Best regards,
Cenvora Team
""",
                related_model='TenantSubscription',
                related_id=str(subscription.id),
            )
            
            activated += 1
            logger.info(f"Auto-activated pending plan for tenant {subscription.tenant.id}")
        except Exception as e:
            logger.error(f"Error activating pending plan for subscription {subscription.id}: {e}")
    
    return {'activated': activated}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=2)
def auto_downgrade_cancelled_subscriptions():
    """
    Scheduled task to downgrade subscriptions marked with cancel_at_period_end=True.
    Run this via Celery Beat daily.
    """
    now = timezone.now()
    downgraded = 0
    
    # Find subscriptions that should downgrade to free
    subscriptions = TenantSubscription.objects.filter(
        cancel_at_period_end=True,
        current_period_end__lte=now,
        status=SubscriptionStatus.ACTIVE,
    )
    
    for subscription in subscriptions:
        try:
            from .models import Plan
            free_plan = Plan.objects.get(code='free')
            
            subscription.plan = free_plan
            subscription.cancel_at_period_end = False
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.current_period_start = now
            subscription.current_period_end = now + timedelta(days=30)
            subscription.save(update_fields=[
                'plan', 'cancel_at_period_end', 'status',
                'current_period_start', 'current_period_end', 'updated_at'
            ])
            
            _sync_legacy_subscription_fields(subscription.tenant, 'free')
            
            # Send notification
            send_async_email_notification.delay(
                user_id=subscription.tenant.id,
                to_email=subscription.tenant.email,
                subject="Plan Expired - Cenvora",
                body=f"""
Hello {subscription.tenant.first_name or subscription.tenant.username},

Your subscription has expired and you have been downgraded to the Free plan.

To upgrade again, visit: https://cenvora.app/profile

Best regards,
Cenvora Team
""",
                related_model='TenantSubscription',
                related_id=str(subscription.id),
            )
            
            downgraded += 1
            logger.info(f"Auto-downgraded subscription for tenant {subscription.tenant.id}")
        except Exception as e:
            logger.error(f"Error downgrading subscription {subscription.id}: {e}")
    
    return {'downgraded': downgraded}

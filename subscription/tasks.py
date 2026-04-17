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
from integration.models import NotificationLog

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


def _extract_failure_reason(payload: dict) -> str:
    if not isinstance(payload, dict):
        return 'Payment processing failed.'

    data = payload.get('data', {}) if isinstance(payload.get('data', {}), dict) else {}
    payment = data.get('payment', {}) if isinstance(data.get('payment', {}), dict) else {}

    return (
        payload.get('error_message')
        or data.get('error_message')
        or payment.get('error_message')
        or payment.get('payment_message')
        or 'Payment processing failed.'
    )


def _expiry_notification_already_sent(user_id, marker: str) -> bool:
    return NotificationLog.objects.filter(
        user_id=user_id,
        channel='email',
        related_model='TenantSubscriptionExpiry',
        related_id=marker,
    ).exists()


def _queue_professional_expiry_email(subscription: TenantSubscription, status_key: str) -> bool:
    tenant = subscription.tenant
    if not tenant.email:
        return False
    if getattr(tenant, 'is_lifetime_free', False):
        return False

    plan_code = (getattr(subscription.plan, 'code', '') or '').lower()
    if plan_code in {'free', 'starter'}:
        return False

    period_end = subscription.current_period_end
    if not period_end:
        return False

    period_end_key = timezone.localtime(period_end).strftime('%Y%m%d%H%M')
    marker = f"{subscription.id}:{status_key}:{period_end_key}"
    if _expiry_notification_already_sent(tenant.id, marker):
        return False

    recipient_name = tenant.first_name or tenant.business_name or tenant.username or 'Customer'
    plan_name = getattr(subscription.plan, 'name', plan_code.title() or 'Current')
    profile_url = 'https://cenvora.app/profile'
    expires_at_text = timezone.localtime(period_end).strftime('%d %b %Y, %I:%M %p')

    if status_key == 'expiring_24h':
        subject = f"Action Needed: Your {plan_name} Plan Expires in 24 Hours | Cenvora"
        body = f"""
Hello {recipient_name},

This is a reminder that your {plan_name} plan is scheduled to expire in approximately 24 hours.

Expiry Time: {expires_at_text}

To avoid any interruption, please renew or upgrade your plan before expiry.

Manage your subscription here: {profile_url}

Regards,
Cenvora Billing Team
support@cenvora.app
"""
    else:
        subject = f"Your {plan_name} Plan Has Expired | Cenvora"
        body = f"""
Hello {recipient_name},

Your {plan_name} plan has now expired.

Expired At: {expires_at_text}

You can renew or activate a paid plan anytime from your profile billing section.

Manage your subscription here: {profile_url}

Regards,
Cenvora Billing Team
support@cenvora.app
"""

    send_async_email_notification.delay(
        user_id=tenant.id,
        to_email=tenant.email,
        subject=subject,
        body=body,
        related_model='TenantSubscriptionExpiry',
        related_id=marker,
    )
    return True


def _set_email_notified(payment: SubscriptionPayment, status_key: str) -> bool:
    """Return True when this status email can be sent now (first time), else False."""
    details = payment.billing_details or {}
    sent = details.get('status_email_sent', [])
    if not isinstance(sent, list):
        sent = []

    if status_key in sent:
        return False

    sent.append(status_key)
    details['status_email_sent'] = sent
    payment.billing_details = details
    payment.save(update_fields=['billing_details', 'updated_at'])
    return True


def _queue_status_email_once(payment: SubscriptionPayment, status_key: str, action_summary: str = '', reason: str = '') -> None:
    if not payment.tenant.email:
        return

    send_payment_status_email.delay(
        payment_id=payment.id,
        status_key=status_key,
        action_summary=action_summary,
        reason=reason,
    )


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

    if payment.status == SubscriptionPaymentStatus.SUCCESS:
        logger.info("Skipping duplicate success webhook for already-processed order %s", order_id)
        return {'status': 'skipped', 'reason': 'already_success'}

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
    
    # Send one-time success status email.
    _queue_status_email_once(payment, status_key='success', action_summary=action_summary)
    
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

    _queue_status_email_once(
        payment,
        status_key='failed',
        reason=_extract_failure_reason(payload),
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

    _queue_status_email_once(payment, status_key='pending')
    
    return {'status': 'pending', 'order_id': order_id}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def send_payment_status_email(self, payment_id: int, status_key: str, action_summary: str = '', reason: str = ''):
    """
    Send a professional Cenvora email for payment status transitions.
    status_key: pending | success | failed
    """
    try:
        payment = SubscriptionPayment.objects.select_related('tenant', 'plan').get(id=payment_id)
    except SubscriptionPayment.DoesNotExist:
        logger.error(f"Payment {payment_id} not found for status email")
        return {'status': 'failed', 'reason': 'payment_not_found'}

    user = payment.tenant
    if not user.email:
        return {'status': 'skipped', 'reason': 'missing_email'}

    if not _set_email_notified(payment, status_key):
        return {'status': 'skipped', 'reason': 'already_notified'}

    recipient_name = user.first_name or user.business_name or user.username or 'Customer'
    order_id = payment.order_id
    plan_name = payment.plan.name
    amount = str(payment.amount)
    profile_url = 'https://cenvora.app/profile'

    if status_key == 'pending':
        subject = 'Payment Received and Pending Confirmation - Cenvora'
        body = f"""
Hello {recipient_name},

We received your payment attempt for the {plan_name} plan, and it is currently pending confirmation from the payment network.

Order ID: {order_id}
Amount: INR {amount}
Status: Pending Verification

No action is required from your side right now. We will automatically notify you once the payment is confirmed as successful or failed.

You can also check your latest status here: {profile_url}

Regards,
Cenvora Billing Team
support@cenvora.app
"""
    elif status_key == 'success':
        subject = 'Payment Successful - Cenvora'
        body = f"""
Hello {recipient_name},

Your payment has been successfully confirmed and your subscription has been updated.

Order ID: {order_id}
Plan: {plan_name}
Amount: INR {amount}
Status: Successful
Update Applied: {action_summary or 'Subscription updated'}

Thank you for continuing with Cenvora.

You can review your subscription details here: {profile_url}

Regards,
Cenvora Billing Team
support@cenvora.app
"""
    else:
        subject = 'Payment Failed - Cenvora'
        body = f"""
Hello {recipient_name},

We could not complete your recent payment.

Order ID: {order_id}
Plan: {plan_name}
Amount: INR {amount}
Status: Failed
Reason: {reason or 'The payment attempt was not authorized/confirmed.'}

Please retry from your profile billing section. If the amount was debited, it is usually auto-reversed by your bank/payment provider as per their timeline.

Retry here: {profile_url}

Regards,
Cenvora Billing Team
support@cenvora.app
"""

    send_async_email_notification.delay(
        user_id=user.id,
        to_email=user.email,
        subject=subject,
        body=body,
        related_model='SubscriptionPayment',
        related_id=order_id,
    )

    logger.info("Queued %s payment status email for order %s", status_key, order_id)
    return {'status': 'queued', 'payment_id': payment_id, 'status_key': status_key}


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


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=2)
def notify_subscription_expiry_windows(self):
    """
    Send professional Cenvora emails for subscription expiry windows:
    - 24 hours before expiry
    - at/after expiry
    Non-blocking via Celery and deduped through NotificationLog markers.
    """
    now = timezone.now()
    reminder_sent = 0
    expired_sent = 0

    subscriptions = TenantSubscription.objects.select_related('tenant', 'plan').filter(
        status=SubscriptionStatus.ACTIVE,
        current_period_end__isnull=False,
    )

    for subscription in subscriptions:
        period_end = subscription.current_period_end
        if not period_end:
            continue

        # Send reminder when within the 24h window and not yet expired.
        seconds_left = (period_end - now).total_seconds()
        if 0 < seconds_left <= 24 * 60 * 60:
            if _queue_professional_expiry_email(subscription, status_key='expiring_24h'):
                reminder_sent += 1

        # Send expired email once period has passed.
        if now >= period_end:
            if _queue_professional_expiry_email(subscription, status_key='expired_now'):
                expired_sent += 1

    return {
        'expiring_24h_sent': reminder_sent,
        'expired_now_sent': expired_sent,
    }

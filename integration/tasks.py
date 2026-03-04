"""
Notification Tasks — Transactional Email API for Email, WhatsApp Business API stub.
TRANSACTIONAL_EMAIL_API_KEY and TRANSACTIONAL_EMAIL_SENDER_EMAIL must be set in .env for production emails.
"""
import logging
import requests as http_requests
from celery import shared_task
from django.conf import settings
from .models import NotificationLog

logger = logging.getLogger(__name__)

# Default API Base URL is now managed in settings.py


# ---- Transactional Email Async Task ----

@shared_task
def send_async_email_notification(user_id, to_email, subject, body, related_model='', related_id=''):
    """
    Asynchronously sends an email via transactional email API.
    Set TRANSACTIONAL_EMAIL_API_KEY and TRANSACTIONAL_EMAIL_SENDER_EMAIL in Django settings/.env.
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)
    except Exception:
        user = None

    log = NotificationLog.objects.create(
        user=user,
        channel='email',
        recipient=to_email,
        subject=subject,
        body=body,
        related_model=related_model,
        related_id=related_id,
        status='queued',
    )

    # Dynamic Sender Configuration
    business_name = user.business_name if user and user.business_name else "Cenvora"
    # Simple sanitization to create businessname@email.cenvora.app
    sanitized_name = "".join(e for e in business_name if e.isalnum()).lower()
    
    API_KEY = getattr(settings, 'TRANSACTIONAL_EMAIL_API_KEY', '')
    FROM_EMAIL = getattr(settings, 'TRANSACTIONAL_EMAIL_SENDER_EMAIL', f"{sanitized_name}@email.cenvora.app")
    FROM_NAME = business_name
    BASE_URL = getattr(settings, 'TRANSACTIONAL_EMAIL_API_URL', '') or 'https://api.ahasend.com/v1'

    if not API_KEY or not BASE_URL:
        # Fallback if API key or URL not set
        logger.info(f"[CONFIG MISSING] API_KEY or URL not set. Check .env settings.")
        log.status = 'failed'
        log.error_message = f"Configuration error: API_KEY={'SET' if API_KEY else 'MISSING'}, URL={'SET' if BASE_URL else 'MISSING'}"
        log.save()
        return {'status': 'failed', 'log_id': str(log.id)}

    try:
        payload = {
            "from": {"email": FROM_EMAIL, "name": FROM_NAME},
            "recipients": [{"email": to_email}],
            "content": {
                "subject": subject,
                "html_body": body.replace("\n", "<br>"),
                "text_body": body,
                "html": body.replace("\n", "<br>"),  # Dual coverage for compatibility
                "text": body,
                "reply_to": {"email": FROM_EMAIL, "name": FROM_NAME},
            },
            "headers": {
                "X-Mailer": "Cenvora-Cloud-Notifier",
                "X-Priority": "3 (Normal)",
            }
        }
        logger.info(f"EMAIL REQUEST: URL={BASE_URL}/email/send | Payload={payload}")
        
        response = http_requests.post(
            f"{BASE_URL}/email/send",
            headers={
                "X-Api-Key": API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        
        logger.info(f"EMAIL RESPONSE: Status={response.status_code} | Body={response.text}")

        # Provider might return 200/201 even if some/all recipients fail
        try:
            res_json = response.json()
            success_count = res_json.get('success_count', 0)
            fail_count = res_json.get('fail_count', 0)
            errors = res_json.get('errors', [])
            
            if success_count > 0:
                log.status = 'sent'
                log.error_message = f"Sent: {success_count} | Failed: {fail_count}"
                if errors:
                    log.error_message += f" | Errors: {', '.join(errors)[:400]}"
            else:
                log.status = 'failed'
                log.error_message = f"Failed to send. Errors: {', '.join(errors)[:500]}"
        except Exception:
            # Fallback for non-JSON or unexpected structure
            if response.status_code in [200, 201, 202]:
                log.status = 'sent'
                log.error_message = f"Provider OK ({response.status_code})"
            else:
                log.status = 'failed'
                log.error_message = f"Provider Error {response.status_code}: {response.text[:500]}"
        
        log.save()
        return {'status': log.status, 'log_id': str(log.id)}
    except Exception as e:
        log.status = 'failed'
        log.error_message = str(e)
        log.save()
        logger.error(f"Email API error: {e}")
        return {'status': 'failed', 'error': str(e), 'log_id': str(log.id)}


# ---- WhatsApp Async Task (Coming Soon - stub only) ----

@shared_task
def send_async_whatsapp_notification(user_id, to_phone, body, related_model='', related_id=''):
    """
    WhatsApp via Meta Business API — Coming Soon.
    Logs the attempt with 'coming_soon' status.
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)
    except Exception:
        user = None

    log = NotificationLog.objects.create(
        user=user,
        channel='whatsapp',
        recipient=to_phone,
        subject='',
        body=body,
        related_model=related_model,
        related_id=related_id,
        status='queued',
    )

    WHATSAPP_API_TOKEN = getattr(settings, 'WHATSAPP_API_TOKEN', '')
    WHATSAPP_PHONE_ID = getattr(settings, 'WHATSAPP_PHONE_ID', '')

    if not WHATSAPP_API_TOKEN or not WHATSAPP_PHONE_ID:
        logger.info(f"[WHATSAPP COMING SOON] To: {to_phone}")
        log.status = 'sent'
        log.error_message = 'WhatsApp integration coming soon. Configure WHATSAPP_API_TOKEN in settings.'
        log.save()
        return {'status': 'coming_soon', 'log_id': str(log.id)}

    try:
        url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": body},
        }
        response = http_requests.post(url, json=payload, headers=headers, timeout=10)
        log.status = 'sent' if response.status_code in [200, 201] else 'failed'
        log.error_message = response.text[:200] if log.status == 'failed' else ''
        log.save()
        return {'status': log.status, 'log_id': str(log.id)}
    except Exception as e:
        log.status = 'failed'
        log.error_message = str(e)
        log.save()
        logger.error(f"WhatsApp API error: {e}")
        return {'status': 'failed', 'error': str(e), 'log_id': str(log.id)}


# ---- Payment Reminder Bulk Task ----

@shared_task
def send_payment_reminders_for_user(user_id, overdue_days=30):
    """
    Sends payment reminder emails to all customers with outstanding balance
    older than overdue_days. Called by scheduled Celery beat task.
    """
    try:
        from django.contrib.auth import get_user_model
        from billing.models import Customer
        from django.utils import timezone
        from datetime import timedelta

        User = get_user_model()
        user = User.objects.get(id=user_id)
    except Exception as e:
        logger.error(f"send_payment_reminders_for_user failed: {e}")
        return {'status': 'error', 'error': str(e)}

    overdue_customers = Customer.objects.filter(
        created_by=user,
        current_balance__gt=0,
        email__isnull=False,
    ).exclude(email='')

    sent = 0
    for customer in overdue_customers:
        subject = f"Payment Reminder — ₹{customer.current_balance} Outstanding"
        body = (
            f"Dear {customer.name},\n\n"
            f"This is a friendly reminder that you have an outstanding balance of "
            f"₹{customer.current_balance} with {user.business_name or 'Cenvora'}.\n\n"
            f"Please clear the dues at your earliest convenience.\n\n"
            f"Thank you!\n— {user.business_name or 'Cenvora'}"
        )
        send_async_email_notification.delay(
            user.id, customer.email, subject, body,
            related_model='Customer', related_id=str(customer.id)
        )
        sent += 1

    logger.info(f"Payment reminders dispatched to {sent} customers for user {user_id}")
    return {'status': 'ok', 'sent': sent}

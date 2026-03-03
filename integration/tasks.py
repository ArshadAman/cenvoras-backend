import logging
from celery import shared_task
from django.conf import settings
from .models import NotificationLog

logger = logging.getLogger(__name__)

# ---- SendGrid Email Async Task ----

@shared_task
def send_async_email_notification(user_id, to_email, subject, body, related_model='', related_id=''):
    """
    Asynchronously sends an email via SendGrid.
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

    SENDGRID_API_KEY = getattr(settings, 'SENDGRID_API_KEY', 'SG.demo_key_replace_me')
    SENDGRID_FROM_EMAIL = getattr(settings, 'SENDGRID_FROM_EMAIL', 'noreply@cenvora.app')

    if SENDGRID_API_KEY.startswith('SG.demo'):
        logger.info(f"[DEMO EMAIL] To: {to_email} | Subject: {subject}")
        log.status = 'sent'
        log.error_message = 'Demo mode — not actually sent'
        log.save()
        return {'status': 'demo_sent', 'log_id': str(log.id)}

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(
            from_email=SENDGRID_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=body,
        )
        response = sg.send(message)
        log.status = 'sent' if response.status_code in [200, 202] else 'failed'
        log.error_message = f"Status: {response.status_code}"
        log.save()
        return {'status': log.status, 'log_id': str(log.id)}
    except Exception as e:
        log.status = 'failed'
        log.error_message = str(e)
        log.save()
        logger.error(f"SendGrid error: {e}")
        return {'status': 'failed', 'error': str(e), 'log_id': str(log.id)}


# ---- WhatsApp Async Task ----

@shared_task
def send_async_whatsapp_notification(user_id, to_phone, body, related_model='', related_id=''):
    """
    Asynchronously sends a WhatsApp message via Meta Business API.
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

    WHATSAPP_API_TOKEN = getattr(settings, 'WHATSAPP_API_TOKEN', 'demo_whatsapp_token')
    WHATSAPP_PHONE_ID = getattr(settings, 'WHATSAPP_PHONE_ID', 'demo_phone_id')

    if WHATSAPP_API_TOKEN == 'demo_whatsapp_token':
        logger.info(f"[DEMO WHATSAPP] To: {to_phone}")
        log.status = 'sent'
        log.error_message = 'Demo mode — not actually sent'
        log.save()
        return {'status': 'demo_sent', 'log_id': str(log.id)}

    try:
        import requests
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
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            log.status = 'sent'
        else:
            log.status = 'failed'
            log.error_message = response.text
        log.save()
        return {'status': log.status, 'log_id': str(log.id)}
    except Exception as e:
        log.status = 'failed'
        log.error_message = str(e)
        log.save()
        logger.error(f"WhatsApp API error: {e}")
        return {'status': 'failed', 'error': str(e), 'log_id': str(log.id)}

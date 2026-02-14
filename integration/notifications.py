"""
Notification Service — SendGrid for Email, WhatsApp Business API stub.
Uses demo API keys by default. Replace with real keys in .env for production.
"""
import logging
from django.conf import settings
from .models import NotificationLog

logger = logging.getLogger(__name__)

# ---- SendGrid Email ----

SENDGRID_API_KEY = getattr(settings, 'SENDGRID_API_KEY', 'SG.demo_key_replace_me')
SENDGRID_FROM_EMAIL = getattr(settings, 'SENDGRID_FROM_EMAIL', 'noreply@cenvora.app')

def send_email(user, to_email, subject, body, related_model='', related_id=''):
    """
    Send email via SendGrid.
    In demo mode (no real key), logs the email instead of sending.
    """
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

    if SENDGRID_API_KEY.startswith('SG.demo'):
        # Demo mode — just log it
        logger.info(f"[DEMO EMAIL] To: {to_email} | Subject: {subject}")
        logger.info(f"[DEMO EMAIL] Body: {body[:200]}...")
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


# ---- WhatsApp (Stub) ----

WHATSAPP_API_TOKEN = getattr(settings, 'WHATSAPP_API_TOKEN', 'demo_whatsapp_token')
WHATSAPP_PHONE_ID = getattr(settings, 'WHATSAPP_PHONE_ID', 'demo_phone_id')

def send_whatsapp(user, to_phone, body, related_model='', related_id=''):
    """
    Send WhatsApp message via Meta Business API.
    In demo mode, logs the message instead of sending.
    """
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

    if WHATSAPP_API_TOKEN == 'demo_whatsapp_token':
        logger.info(f"[DEMO WHATSAPP] To: {to_phone} | Body: {body[:200]}...")
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


# ---- Template Renderer ----

def render_template(template_body, context):
    """Replace {{placeholders}} in template body with context values."""
    result = template_body
    for key, value in context.items():
        result = result.replace(f'{{{{{key}}}}}', str(value))
    return result


def send_invoice_notification(user, invoice, channels=None):
    """
    High-level: Send invoice notification via configured channels.
    """
    if channels is None:
        channels = ['email']

    customer = invoice.customer
    if not customer:
        return {'error': 'No customer on invoice'}

    context = {
        'customer_name': customer.name or 'Customer',
        'invoice_number': invoice.invoice_number,
        'amount': str(invoice.total_amount),
        'business_name': user.business_name or 'Cenvora',
        'date': str(invoice.invoice_date),
    }

    subject = f"Invoice {invoice.invoice_number} from {context['business_name']}"
    body = (
        f"Dear {context['customer_name']},\n\n"
        f"Your invoice {context['invoice_number']} for ₹{context['amount']} "
        f"dated {context['date']} has been generated.\n\n"
        f"Thank you for your business!\n"
        f"— {context['business_name']}"
    )

    results = []
    if 'email' in channels and customer.email:
        results.append(send_email(user, customer.email, subject, body,
                                  related_model='SalesInvoice', related_id=str(invoice.id)))
    if 'whatsapp' in channels and customer.phone:
        results.append(send_whatsapp(user, customer.phone, body,
                                     related_model='SalesInvoice', related_id=str(invoice.id)))

    return results

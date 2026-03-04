"""
Notification Service — Email Service API for Email, WhatsApp Business API stub.
Uses demo API keys by default. Replace with real keys in .env for production.
"""
import logging
from django.conf import settings
from .models import NotificationLog

logger = logging.getLogger(__name__)

from .tasks import send_async_email_notification

def send_email(user, to_email, subject, body, related_model='', related_id=''):
    """
    Dispatches email to Celery worker.
    """
    user_id = user.id if user else None
    # Dispatch asynchronously
    send_async_email_notification.delay(user_id, to_email, subject, body, related_model, related_id)
    return {'status': 'queued'}


from .tasks import send_async_whatsapp_notification

def send_whatsapp(user, to_phone, body, related_model='', related_id=''):
    """
    Dispatches WhatsApp message to Celery worker via Meta Business API.
    """
    user_id = user.id if user else None
    # Dispatch asynchronously
    send_async_whatsapp_notification.delay(user_id, to_phone, body, related_model, related_id)
    return {'status': 'queued'}


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

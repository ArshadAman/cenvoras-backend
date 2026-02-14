from django.db import models
from django.conf import settings
import uuid
import secrets

class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=100, help_text="e.g. My Online Store")
    key = models.CharField(max_length=64, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.user})"


# =============================================================================
# NOTIFICATION SYSTEM (WhatsApp + Email via SendGrid)
# =============================================================================

class NotificationTemplate(models.Model):
    """Reusable message templates for WhatsApp and Email."""
    CHANNEL_CHOICES = [
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
        ('both', 'Both'),
    ]
    EVENT_CHOICES = [
        ('invoice_created', 'Invoice Created'),
        ('payment_received', 'Payment Received'),
        ('payment_reminder', 'Payment Reminder'),
        ('order_update', 'Order Status Update'),
        ('welcome', 'Welcome Message'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_templates')
    name = models.CharField(max_length=100)
    event = models.CharField(max_length=30, choices=EVENT_CHOICES)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default='email')
    subject = models.CharField(max_length=200, blank=True, help_text="Email subject line (ignored for WhatsApp)")
    body = models.TextField(help_text="Use {{customer_name}}, {{invoice_number}}, {{amount}}, {{business_name}} as placeholders")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.event})"


class NotificationLog(models.Model):
    """Tracks every notification sent."""
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
    ]
    CHANNEL_CHOICES = [
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_logs')
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    recipient = models.CharField(max_length=255, help_text="Email or phone number")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='queued')
    error_message = models.TextField(blank=True)
    
    # Reference to what triggered it
    related_model = models.CharField(max_length=50, blank=True, help_text="e.g. SalesInvoice")
    related_id = models.CharField(max_length=50, blank=True)
    
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.channel} to {self.recipient} ({self.status})"

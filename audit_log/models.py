from django.db import models
from django.conf import settings
import uuid
from django.utils.translation import gettext_lazy as _

class AuditLog(models.Model):
    ACTION_CHOICES = (
        ('CREATE', _('Create')),
        ('UPDATE', _('Update')),
        ('DELETE', _('Delete')),
        ('LOGIN', _('Login')),
        ('LOGOUT', _('Logout')),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='audit_logs'
    )
    user_email = models.EmailField(blank=True, null=True, help_text="Snapshot of user email in case user is deleted")
    
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100, help_text="e.g. SalesInvoice, Product")
    object_id = models.CharField(max_length=50, blank=True, null=True)
    object_repr = models.CharField(max_length=255, blank=True, null=True, help_text="String representation of the object")
    
    changes = models.JSONField(default=dict, blank=True, help_text="Stores 'before' and 'after' state for updates")
    
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = _('Audit Log')
        verbose_name_plural = _('Audit Logs')

    def __str__(self):
        return f"{self.user_email} - {self.action} {self.model_name} ({self.timestamp})"

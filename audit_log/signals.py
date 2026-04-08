from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.forms.models import model_to_dict
from .models import AuditLog
from .middleware import get_current_user, get_current_request
import json
from decimal import Decimal
from datetime import date, datetime
import uuid

# Models to exclude from logging to avoid noise/recursion
EXCLUDED_MODELS = [
    'AuditLog', 'Session', 'LogEntry', 'Migration', 'ContentType', 
    'Permission', 'Group', 'Token', 'OutstandingToken', 'BlacklistedToken'
]

class AuditJSONEncoder(json.JSONEncoder):
    """
    JSON Encoder for non-serializable objects (Decimal, Date, UUID)
    """
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@receiver(post_save)
def audit_log_save(sender, instance, created, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return
        
    user = get_current_user()
    request = get_current_request()
    
    # If no user context (e.g. management command), we can still log if needed, 
    # but usually we want to track user actions. 
    # For now, let's log even if user is None (system action).
    
    action = 'CREATE' if created else 'UPDATE'
    
    # For Updates, we ideally want to know WHAT changed.
    # But post_save doesn't give us the 'old' instance easily without pre_save caching.
    # For now, we'll log the 'snapshot' of the current state.
    # Implementing full diff requires pre_save signal or __init__ tracking.
    
    try:
        # Simple serialization
        changes = model_to_dict(instance)
        # Filter out binary fields or huge text fields if needed
    except:
        changes = {}

    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        user_email=getattr(user, 'email', 'system') if user and getattr(user, 'is_authenticated', False) else 'system',
        action=action,
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=json.loads(json.dumps(changes, cls=AuditJSONEncoder)),
        ip_address=get_client_ip(request) if request else None
    )

@receiver(post_delete)
def audit_log_delete(sender, instance, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return

    user = get_current_user()
    request = get_current_request()

    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        user_email=getattr(user, 'email', 'system') if user and getattr(user, 'is_authenticated', False) else 'system',
        action='DELETE',
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes={}, # Deleted, no changes to track, or log snapshot
        ip_address=get_client_ip(request) if request else None
    )

from threading import local
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
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)

def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

# Store old instance state for diffing
_old_instances = local()

@receiver(post_save)
def audit_log_save(sender, instance, created, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return
        
    request = get_current_request()
    user = getattr(request, 'user', None) if request else get_current_user()
    
    action = 'CREATE' if created else 'UPDATE'
    
    changes = {}
    try:
        new_state = model_to_dict(instance)
        if created:
            changes = new_state
        else:
            # Try to get old state from thread local
            old_state = getattr(_old_instances, str(instance.pk), None)
            if old_state:
                for field, new_val in new_state.items():
                    old_val = old_state.get(field)
                    if old_val != new_val:
                        changes[field] = {'old': old_val, 'new': new_val}
                # Cleanup
                delattr(_old_instances, str(instance.pk))
            else:
                # Fallback if pre_save didn't catch it
                changes = new_state
    except:
        changes = {}

    if request and request.path.startswith('/admin/'):
        return

    if action == 'UPDATE' and not changes:
        return # Nothing changed

    AuditLog.objects.create(
        tenant=getattr(user, 'active_tenant', None) if user and getattr(user, 'is_authenticated', False) else None,
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        user_email=getattr(user, 'email', 'system') if user and getattr(user, 'is_authenticated', False) else 'system',
        action=action,
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=json.loads(json.dumps(changes, cls=AuditJSONEncoder)),
        ip_address=get_client_ip(request) if request else None
    )

from django.db.models.signals import pre_save
@receiver(pre_save)
def audit_log_pre_save(sender, instance, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            setattr(_old_instances, str(instance.pk), model_to_dict(old_instance))
        except:
            pass

@receiver(post_delete)
def audit_log_delete(sender, instance, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return

    request = get_current_request()
    user = getattr(request, 'user', None) if request else get_current_user()

    if request and request.path.startswith('/admin/'):
        return

    AuditLog.objects.create(
        tenant=getattr(user, 'active_tenant', None) if user and getattr(user, 'is_authenticated', False) else None,
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        user_email=getattr(user, 'email', 'system') if user and getattr(user, 'is_authenticated', False) else 'system',
        action='DELETE',
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes={}, # Deleted, no changes to track, or log snapshot
        ip_address=get_client_ip(request) if request else None
    )

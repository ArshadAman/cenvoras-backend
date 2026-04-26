from django.db.models.signals import post_save, post_delete, pre_save
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

def get_diff(old, new):
    """
    Returns a dict of changed fields: {field: {'old': val, 'new': val}}
    """
    diff = {}
    for key, value in new.items():
        if key.startswith('_'): continue
        old_value = old.get(key)
        # Handle decimal/string comparisons
        if str(old_value) != str(value):
            diff[key] = {
                'old': old_value,
                'new': value
            }
    return diff

@receiver(pre_save)
def audit_log_pre_save(sender, instance, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return
    if instance.pk:
        try:
            # We don't use objects.get here because it might trigger more signals or be slow
            # But we need the old state. 
            old_instance = sender.objects.filter(pk=instance.pk).first()
            if old_instance:
                instance._old_state = model_to_dict(old_instance)
        except:
            instance._old_state = {}

@receiver(post_save)
def audit_log_save(sender, instance, created, **kwargs):
    if sender.__name__ in EXCLUDED_MODELS:
        return
        
    user = get_current_user()
    request = get_current_request()
    
    action = 'CREATE' if created else 'UPDATE'
    
    try:
        new_state = model_to_dict(instance)
        if created:
            changes = new_state
        else:
            old_state = getattr(instance, '_old_state', {})
            changes = get_diff(old_state, new_state)
            if not changes:
                return # No actual changes, don't log
    except:
        changes = {}

    # Resolve tenant: prioritize the object's tenant, fallback to user's tenant
    tenant = None
    if hasattr(instance, 'tenant') and instance.tenant:
        tenant = instance.tenant
    elif hasattr(instance, 'user') and instance.user:
        tenant = getattr(instance.user, 'active_tenant', instance.user)
    elif user and getattr(user, 'is_authenticated', False):
        tenant = getattr(user, 'active_tenant', user)

    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        tenant=tenant,
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

    # Resolve tenant: prioritize the object's tenant, fallback to user's tenant
    tenant = None
    if hasattr(instance, 'tenant') and instance.tenant:
        tenant = instance.tenant
    elif hasattr(instance, 'user') and instance.user:
        tenant = getattr(instance.user, 'active_tenant', instance.user)
    elif user and getattr(user, 'is_authenticated', False):
        tenant = getattr(user, 'active_tenant', user)

    # Capture snapshot of what was deleted
    try:
        snapshot = model_to_dict(instance)
    except:
        snapshot = {}

    AuditLog.objects.create(
        user=user if user and getattr(user, 'is_authenticated', False) else None,
        tenant=tenant,
        user_email=getattr(user, 'email', 'system') if user and getattr(user, 'is_authenticated', False) else 'system',
        action='DELETE',
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=json.loads(json.dumps(snapshot, cls=AuditJSONEncoder)),
        ip_address=get_client_ip(request) if request else None
    )

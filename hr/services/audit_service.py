# Audit service — AuditLog write helpers for HR operations
# Implemented in task 4.1

from audit_log.models import AuditLog


def _get_client_ip(request):
    """Extract client IP from request, checking X-Forwarded-For first."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _get_tenant(request):
    """Return the active tenant for the requesting user."""
    return getattr(request.user, 'active_tenant', request.user)


def log_create(request, instance, model_name=None, changes=None):
    """
    Record a CREATE audit log entry for an HR model instance.

    Args:
        request: The Django HTTP request object.
        instance: The model instance that was created.
        model_name: Optional override for the model name string.
        changes: Optional dict of initial field values to store.
    """
    AuditLog.objects.create(
        tenant=_get_tenant(request),
        user=request.user,
        user_email=getattr(request.user, 'email', ''),
        action='CREATE',
        model_name=model_name or instance.__class__.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes or {},
        ip_address=_get_client_ip(request),
    )


def log_update(request, instance, before, after, model_name=None):
    """
    Record an UPDATE audit log entry for an HR model instance.

    Args:
        request: The Django HTTP request object.
        instance: The model instance that was updated.
        before: Dict of field values before the update.
        after: Dict of field values after the update.
        model_name: Optional override for the model name string.
    """
    AuditLog.objects.create(
        tenant=_get_tenant(request),
        user=request.user,
        user_email=getattr(request.user, 'email', ''),
        action='UPDATE',
        model_name=model_name or instance.__class__.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes={'before': before, 'after': after},
        ip_address=_get_client_ip(request),
    )


def log_delete(request, instance, model_name=None):
    """
    Record a DELETE audit log entry for an HR model instance.

    Args:
        request: The Django HTTP request object.
        instance: The model instance that was deleted.
        model_name: Optional override for the model name string.
    """
    AuditLog.objects.create(
        tenant=_get_tenant(request),
        user=request.user,
        user_email=getattr(request.user, 'email', ''),
        action='DELETE',
        model_name=model_name or instance.__class__.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes={},
        ip_address=_get_client_ip(request),
    )


def log_download(request, instance, model_name=None):
    """
    Record a DOWNLOAD audit log entry for an HR model instance (e.g. Payslip PDF).

    Args:
        request: The Django HTTP request object.
        instance: The model instance that was downloaded.
        model_name: Optional override for the model name string.
    """
    AuditLog.objects.create(
        tenant=_get_tenant(request),
        user=request.user,
        user_email=getattr(request.user, 'email', ''),
        action='DOWNLOAD',
        model_name=model_name or instance.__class__.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes={},
        ip_address=_get_client_ip(request),
    )

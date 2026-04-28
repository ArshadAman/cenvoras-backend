from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from billing.models import SalesInvoice, PurchaseBill
from .models import ActionLog, User
import json

def get_current_user():
    # Helper to get user context? 
    # Signals are async from request, so we can't easily get 'request.user' inside a signal without middleware/threadlocal.
    # For now, we will inspect the instance.created_by if available, OR relying on implicit context.
    # Actually, SalesInvoice and PurchaseBill have 'created_by'.
    return None

@receiver(post_save, sender=SalesInvoice)
def log_sales_invoice_change(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    user = instance.created_by # This is reliable for CREATE. For UPDATE, might be different user? 
                               # Major Enterprise Gaps: 'created_by' doesn't change on edit.
                               # We will assume 'created_by' is the owner for now. 
                               # Real auditing needs Middleware to capture request.user.
    
    ActionLog.objects.create(
        user=user,
        action=action,
        model_name="SalesInvoice",
        object_id=str(instance.id),
        details={
            "invoice_number": instance.invoice_number,
            "total_amount": float(instance.total_amount)
        }
    )

@receiver(post_save, sender=PurchaseBill)
def log_purchase_bill_change(sender, instance, created, **kwargs):
    action = "CREATE" if created else "UPDATE"
    ActionLog.objects.create(
        user=instance.created_by,
        action=action,
        model_name="PurchaseBill",
        object_id=str(instance.id),
        details={
            "bill_number": instance.bill_number,
            "total_amount": float(instance.total_amount)
        }
    )


@receiver(post_save, sender=User)
def sync_vip_subscription(sender, instance, created, update_fields, **kwargs):
    """
    When is_lifetime_free changes, invalidate the subscription entitlements cache
    so the new plan (business or fallback) takes effect immediately.
    """
    # Only act when is_lifetime_free was explicitly saved
    if update_fields is not None and 'is_lifetime_free' not in update_fields:
        return

    try:
        from subscription.services import invalidate_subscription_cache
        invalidate_subscription_cache(instance)
    except Exception:
        pass  # Never break a save due to cache issues

from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver
from .models import StockTransfer, StockPoint, Warehouse

@receiver(post_save, sender=StockPoint)
def sync_product_stock_on_stockpoint_save(sender, instance, **kwargs):
    """
    Keep product cached stock in sync whenever a StockPoint quantity changes.
    """
    try:
        if instance.batch_id and instance.batch and instance.batch.product:
            instance.batch.product.recalculate_stock(save=True)
    except Exception:
        pass

@receiver(post_delete, sender=StockPoint)
def sync_product_stock_on_stockpoint_delete(sender, instance, **kwargs):
    """
    Keep product cached stock in sync whenever a StockPoint is deleted.
    """
    try:
        if instance.batch_id and instance.batch and instance.batch.product:
            instance.batch.product.recalculate_stock(save=True)
    except Exception:
        pass

@receiver(post_save, sender=StockTransfer)
def process_stock_transfer(sender, instance, created, **kwargs):
    """
    Execute stock movement when an existing transfer is marked completed.
    Create path applies stock in StockTransferSerializer (items must exist first).
    """
    if created or instance.status != 'completed':
        return

    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    # Skip when serializer already applied stock then set status=completed
    if getattr(instance, '_stock_applied_inline', False):
        return

    from .stock_sync import reconcile_product_stock

    for item in instance.items.select_related('product', 'batch').all():
        source_stock, _ = StockPoint.objects.select_for_update().get_or_create(
            warehouse=instance.source_warehouse,
            batch=item.batch,
            defaults={'quantity': 0},
        )
        source_stock.quantity = int(source_stock.quantity or 0) - int(item.quantity)
        source_stock.save(update_fields=['quantity', 'last_updated'])

        dest_stock, _ = StockPoint.objects.select_for_update().get_or_create(
            warehouse=instance.destination_warehouse,
            batch=item.batch,
            defaults={'quantity': 0},
        )
        dest_stock.quantity = int(dest_stock.quantity or 0) + int(item.quantity)
        dest_stock.save(update_fields=['quantity', 'last_updated'])

        reconcile_product_stock(item.product, save=True)


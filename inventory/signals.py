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
    Execute stock movement when transfer is marked as completed.
    """
    if instance.status == 'completed':
        print(f"DEBUG: Processing Stock Transfer {instance.id}")
        
        # Iterate through items and move stock
        for item in instance.items.all():
            try:
                # Decrease from Source
                source_stock, _ = StockPoint.objects.get_or_create(
                    warehouse=instance.source_warehouse,
                    batch=item.batch,
                    defaults={'quantity': 0}
                )
                source_stock.quantity -= item.quantity
                source_stock.save()
                print(f"DEBUG: Decreased {item.quantity} from {instance.source_warehouse.name}")

                # Increase at Destination
                dest_stock, _ = StockPoint.objects.get_or_create(
                    warehouse=instance.destination_warehouse,
                    batch=item.batch,
                    defaults={'quantity': 0}
                )
                dest_stock.quantity += item.quantity
                dest_stock.save()
                print(f"DEBUG: Increased {item.quantity} at {instance.destination_warehouse.name}")
                
            except Exception as e:
                print(f"ERROR processing item {item.id}: {e}")
                # Ideally, we should rollback here, but signals are already in transaction if atomic/
                raise e


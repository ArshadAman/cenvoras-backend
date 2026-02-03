from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import StockTransfer, StockPoint, Warehouse

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

# Actually, relying on post_save of Transfer might be too early if items aren't added yet.
# Better to have a dedicated 'complete_transfer' action or signal on the Item itself?
# Or assume the API creates items then updates status to completed.

import uuid
from django.db import models
from django.conf import settings

# Create your models here.

class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    unit = models.CharField(max_length=20, default="pcs")  # e.g., pcs, kg, box
    
    # Unit Conversion (Phase 4)
    secondary_unit = models.CharField(max_length=20, blank=True, null=True, help_text="e.g., Box")
    conversion_factor = models.PositiveIntegerField(default=1, help_text="1 Secondary Unit = X Primary Units")
    
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # GST %
    stock = models.PositiveIntegerField(default=0, help_text="Global stock count (Cached)")
    low_stock_alert = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

    def recalculate_stock(self):
        """
        Re-aggregates total stock from all StockPoints (Warehouses/Batches).
        Updates the cached 'stock' field.
        """
        from django.db.models import Sum
        # Avoid circular import by importing inside method if needed, 
        # though StockPoint relies on Product so it's tricky.
        # Actually StockPoint is in this file (later), so we can use string reference or import after class definition.
        # But since they are in same file, we can use 'StockPoint' name directly if defined, OR self.batches.stock_points...
        
        # Better approach: 
        # total = StockPoint.objects.filter(batch__product=self).aggregate(total=Sum('quantity'))['total'] or 0
        pass # Will implement properly after StockPoint is defined or use reverse relation logic carefully.

class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class ProductBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    batch_number = models.CharField(max_length=50)
    expiry_date = models.DateField(null=True, blank=True)
    manufacturing_date = models.DateField(null=True, blank=True)
    
    # Batch-specific pricing (Marg parity)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Maximum Retail Price for this batch")
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Purchase cost for this batch")
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Selling price for this batch")
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['product', 'batch_number']
        ordering = ['expiry_date']  # Default to FEFO (First Expiry First Out)

    def save(self, *args, **kwargs):
        # Feature 24: Separator Batch — sanitize batch names
        import re
        if self.batch_number:
            self.batch_number = self.batch_number.strip()
            # Normalize multiple separators (---, ///, ___) to single dash
            self.batch_number = re.sub(r'[-_/\\]{2,}', '-', self.batch_number)
            # Remove leading/trailing separators
            self.batch_number = self.batch_number.strip('-_/')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.batch_number} (Exp: {self.expiry_date})"

class StockPoint(models.Model):
    """
    Tracks quantity of a specific batch in a specific warehouse.
    This is the core of the Multi-Warehouse + Batch inventory system.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ProductBatch, on_delete=models.CASCADE, related_name='stock_points')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='stock_points')
    quantity = models.IntegerField(default=0)
    
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('warehouse', 'batch')

    def __str__(self):
        return f"{self.warehouse.name} - {self.batch.product.name} ({self.batch.batch_number}): {self.quantity}"

class StockTransfer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_warehouse = models.ForeignKey(Warehouse, related_name='transfers_out', on_delete=models.PROTECT)
    destination_warehouse = models.ForeignKey(Warehouse, related_name='transfers_in', on_delete=models.PROTECT)
    transfer_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, default='completed', choices=[('pending', 'Pending'), ('completed', 'Completed')])
    notes = models.TextField(blank=True, null=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transfer {self.id} ({self.source_warehouse} -> {self.destination_warehouse})"

class StockTransferItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(StockTransfer, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product.name} ({self.quantity})"

# Signal to reconcile stock? Or keep it manual? 
# For now, let's attach the method to Product properly.
def product_recalculate_stock(self):
    """
    Re-aggregates total stock from all StockPoints.
    """
    from django.db.models import Sum
    # Sum up all quantities from stock points linked to this product's batches
    total = StockPoint.objects.filter(batch__product=self).aggregate(total=Sum('quantity'))['total'] or 0
    self.stock = total
    self.save(update_fields=['stock'])


Product.recalculate_stock = product_recalculate_stock

# Import Sidecar Models to ensure they are registered
# Import Sidecar Models to ensure they are registered
from .models_sidecar import ProductMeta, ProductBatchMeta, BillOfMaterial, StockJournal, StockJournalItem
from .models_pricing import PriceList, PriceListItem, Scheme

from django.db import models
from django.conf import settings
import uuid
from .models import Product, ProductBatch, Warehouse

# =============================================================================
# SIDECAR MODELS (Module 1 - Inventory Extensions)
# =============================================================================

class ProductMeta(models.Model):
    """
    Sidecar for Product. Stores all extra fields to keep core Product table clean.
    Ref: Features 12, 14, 16, 20, 21, 26, 37, 53, 66 (Partially)
    """
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='meta')
    
    # Feature 12: Secondary Stock (held at distributor)
    secondary_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Stock held at distributor/secondary location")
    
    # Feature 14: Alternate/Substitute Search
    tags = models.JSONField(default=list, blank=True, help_text="List of tags/substitutes for search")
    
    # Feature 16: Pharmacy H1 & Narcotics
    is_h1 = models.BooleanField(default=False, help_text="Schedule H1 Drug")
    is_narcotic = models.BooleanField(default=False, help_text="Narcotic Drug")
    
    # Feature 20: Krishi Mandi (Bags)
    # Billing Logic: Qty = Bags * (UnitWeight - Tare)
    bag_weight = models.DecimalField(max_digits=10, decimal_places=3, default=0, help_text="Weight per bag")
    tare_weight = models.DecimalField(max_digits=10, decimal_places=3, default=0, help_text="Empty bag weight")
    
    # Feature 21: Mandi Expenses
    mandi_tax = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Mandi Tax %")
    labour_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Labour charge per unit/bag")
    
    # Feature 26: New Launched Items
    is_new_launch = models.BooleanField(default=False, help_text="Highlight in UI as New Launch")
    
    # Feature 37: Salt Master
    salt_composition = models.TextField(blank=True, null=True, help_text="Chemical composition for pharmacy search")
    
    # Feature 53: Bundle/Kit Formula
    # Structure: [{"product_id": "uuid", "qty": 1}, ...]
    bundle_items = models.JSONField(default=list, blank=True, help_text="Items to auto-add when this product is billed")
    
    # Feature 72: Barcode/QR
    barcode = models.CharField(max_length=100, blank=True, null=True, unique=True, help_text="EAN/UPC barcode for scanning")
    
    # Feature 66: Returnable Packaging (Caret/Crate)
    is_returnable_packaging = models.BooleanField(default=False, help_text="Product uses returnable crates/containers")
    crate_qty = models.PositiveIntegerField(default=0, help_text="Number of crates/containers issued")
    crate_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Deposit rate per crate")
    
    def __str__(self):
        return f"Meta for {self.product.name}"

class ProductBatchMeta(models.Model):
    """
    Sidecar for ProductBatch. Stores dates and extra batch info.
    Ref: Features 17, 18
    """
    batch = models.OneToOneField(ProductBatch, on_delete=models.CASCADE, related_name='meta')
    
    # Feature 17: Mfg Date
    mfg_date = models.DateField(null=True, blank=True)
    
    # Feature 18: Expiry Date (Note: ProductBatch already had expiry_date in core, but we can sync or use this)
    # The prompt explicitly asked to "Add mfg_date... Add exp_date". 
    # Since core `ProductBatch` has `expiry_date`, we will rely on that for core logic, 
    # but `mfg_date` definitely goes here. 
    # If users want strict separation, we can put extended date logic here.
    
    def __str__(self):
        return f"Meta for {self.batch.batch_number}"

class BillOfMaterial(models.Model):
    """
    Feature 8: Conversion Entry (BOM).
    Logic: Deduct Raw Material, Add Finished Good.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Refined: Can be a linked Product OR just a plain text name
    finished_good = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='boms')
    finished_good_name = models.CharField(max_length=255, blank=True, help_text="Plain text name if no product is linked")
    
    name = models.CharField(max_length=100, help_text="BOM Name e.g. 'Standard Pack'")
    is_active = models.BooleanField(default=True)
    
    # Components structure: [{"product_id": "uuid", "quantity": 1}, ...]
    components = models.JSONField(default=list)
    
    # Feature 12: Expanded BOM info
    production_time = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., 2 Hours, 1 Day")
    batch_size = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    testing_notes = models.TextField(blank=True, null=True, help_text="Quality testing notes for the finished good")
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        fg_display = self.finished_good.name if self.finished_good else self.finished_good_name
        return f"BOM: {self.name} for {fg_display}"

class StockJournal(models.Model):
    """
    Feature 11: All Inventory Voucher.
    Generic stock adjustment (+/-) without financial impact.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    voucher_no = models.CharField(max_length=50, blank=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    
    # Adjustment Type: 'excess', 'shortage', 'damage', 'internal_use'
    adjustment_type = models.CharField(max_length=50) 
    
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"StockJournal {self.voucher_no} ({self.date})"

class StockJournalItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.ForeignKey(StockJournal, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.PROTECT)
    
    # Quantity can be positive (Access) or negative (Shortage)
    quantity = models.IntegerField(help_text="Positive for Add, Negative for Deduct")
    
    def __str__(self):
        return f"{self.product.name}: {self.quantity}"

"""
Sales Return (Credit Note) & Purchase Return (Debit Note) Models.
These are standard GST-compliant return vouchers.
"""
from django.db import models
from django.conf import settings
import uuid
from .models import SalesInvoice, PurchaseBill, Customer
from inventory.models import Product, ProductBatch, Warehouse


class CreditNote(models.Model):
    """
    Sales Return — Credit Note issued to customer.
    Reduces customer balance, increases stock.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credit_note_number = models.CharField(max_length=100)
    date = models.DateField()
    
    # Link to original invoice (optional)
    original_invoice = models.ForeignKey(
        SalesInvoice, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='credit_notes', help_text="Original invoice being credited"
    )
    
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='credit_notes')
    
    REASON_CHOICES = [
        ('return', 'Goods Returned'),
        ('defective', 'Defective Goods'),
        ('discount', 'Post-Sale Discount'),
        ('rate_diff', 'Rate Difference'),
        ('other', 'Other'),
    ]
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='return')
    notes = models.TextField(blank=True)
    
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Warehouse where returned goods are received"
    )
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"CN-{self.credit_note_number} ({self.customer.name})"


class CreditNoteItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credit_note = models.ForeignKey(CreditNote, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True)
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)


class DebitNote(models.Model):
    """
    Purchase Return — Debit Note issued to vendor.
    Reduces amount owed to vendor, decreases stock.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    debit_note_number = models.CharField(max_length=100)
    date = models.DateField()
    
    # Link to original bill (optional)
    original_bill = models.ForeignKey(
        PurchaseBill, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='debit_notes', help_text="Original purchase bill being debited"
    )
    
    vendor_name = models.CharField(max_length=255)
    vendor_gstin = models.CharField(max_length=15, blank=True, null=True)
    
    REASON_CHOICES = [
        ('return', 'Goods Returned'),
        ('defective', 'Defective Goods'),
        ('rate_diff', 'Rate Difference'),
        ('shortage', 'Short Supply'),
        ('other', 'Other'),
    ]
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='return')
    notes = models.TextField(blank=True)
    
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Warehouse from where goods are returned"
    )
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DN-{self.debit_note_number} ({self.vendor_name})"


class DebitNoteItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    debit_note = models.ForeignKey(DebitNote, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True)
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

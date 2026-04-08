from django.db import models
from django.conf import settings
import uuid

# Import Product from inventory
# Import Product from inventory
from inventory.models import Product, ProductBatch, Warehouse
from cenvoras.constants import IndianStates

class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    gstin = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    
    # Financial Controls (Marg Parity)
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Maximum allowed credit")
    current_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Positive = Customer Owes Us")
    allow_credit = models.BooleanField(default=True, help_text="If false, block sales when limit exceeded")
    
    state = models.CharField(
        max_length=2, 
        choices=IndianStates.choices, 
        blank=True, 
        null=True,
        help_text="Customer's State (Determines IGST vs CGST/SGST)"
    )
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return self.name

    def __str__(self):
        return self.name

class PaymentMode(models.TextChoices):
    CASH = 'cash', 'Cash'
    UPI = 'upi', 'UPI'
    BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
    CHEQUE = 'cheque', 'Cheque'

class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments')
    invoice = models.ForeignKey('SalesInvoice', on_delete=models.SET_NULL, null=True, blank=True, related_name='invoice_payments')
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.CASH)
    reference = models.CharField(max_length=100, blank=True, help_text="Cheque No / UPI Transaction ID")
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.name} - {self.amount} ({self.date})"

class Vendor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    gstin = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    
    state = models.CharField(
        max_length=2, 
        choices=IndianStates.choices, 
        blank=True, 
        null=True,
        help_text="Vendor's State (Determines IGST vs CGST/SGST)"
    )
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return self.name

class PurchaseBill(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bill_number = models.CharField(max_length=100)
    bill_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, null=True, blank=True)
    vendor_name = models.CharField(max_length=255)
    vendor_address = models.TextField(blank=True, null=True)
    vendor_gstin = models.CharField(max_length=15, blank=True, null=True)
    gst_treatment = models.CharField(max_length=50, blank=True, null=True)
    journal = models.CharField(max_length=50, default="Purchases")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, help_text="Warehouse where items are received")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

class PurchaseBillItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_bill = models.ForeignKey(PurchaseBill, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific batch being purchased")
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # Scheme Support (Phase 6)
    free_quantity = models.PositiveIntegerField(default=0, help_text="Qty received free under scheme")


class SalesInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True)
    customer_name = models.CharField(max_length=255, null=True, blank=True)  # Always store customer name as text
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    delivery_address = models.TextField(blank=True, null=True)
    
    # Tax fields
    place_of_supply = models.CharField(
        max_length=2, 
        choices=IndianStates.choices, 
        blank=True, 
        null=True,
        help_text="State code where goods are supplied"
    )
    
    gst_treatment = models.CharField(max_length=50, blank=True, null=True)
    journal = models.CharField(max_length=50, default="Sales")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, help_text="Warehouse from where items are sold")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

class SalesInvoiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sales_invoice = models.ForeignKey(SalesInvoice, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific batch being sold")
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Scheme Support (Phase 6)
    free_quantity = models.PositiveIntegerField(default=0, help_text="Qty given free under scheme (Buy X Get Y)")

# Import Sidecar Models to ensure they are registered
from .models_sidecar import TransactionMeta, InvoiceSettings, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, PurchaseIndent, PurchaseIndentItem
from .models_returns import CreditNote, CreditNoteItem, DebitNote, DebitNoteItem

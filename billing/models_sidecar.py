from django.db import models
from django.conf import settings
import uuid
from .models import SalesInvoice, Customer
from inventory.models import Product, Warehouse

# =============================================================================
# SIDECAR MODELS (Module 3 - Party & CRM Engine)
# =============================================================================

class PartyMeta(models.Model):
    """
    Sidecar for Customer. Stores CRM & extra financial info.
    Ref: Features 13 (Credit Limit - in core), 28 (Loyalty), 49 (Category)
    """
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name='meta')
    
    # Feature 28: Loyalty Points
    loyalty_points = models.PositiveIntegerField(default=0, help_text="Accrued loyalty points")
    
    # Feature 49: Party Category
    PARTY_CATEGORY_CHOICES = [
        ('retailer', 'Retailer'),
        ('wholesaler', 'Wholesaler'),
        ('distributor', 'Distributor'),
        ('consumer', 'End Consumer'),
    ]
    party_category = models.CharField(
        max_length=20, 
        choices=PARTY_CATEGORY_CHOICES, 
        default='consumer',
        help_text="Classification for pricing and reporting"
    )
    
    # Financials
    credit_days = models.PositiveIntegerField(default=0, help_text="Default payment terms in days")
    gst_type = models.CharField(
        max_length=20, 
        choices=[('registered', 'Registered'), ('unregistered', 'Unregistered'), ('composite', 'Composite')],
        default='unregistered'
    )
    
    # Contact
    whatsapp_number = models.CharField(max_length=20, blank=True, null=True)
    
    def __str__(self):
        return f"Meta for {self.customer.name}"


# =============================================================================
# SIDECAR MODELS (Module 2 - Transaction Extensions)
# =============================================================================

class TransactionMeta(models.Model):
    """
    Sidecar for SalesInvoice. Stores approvals, settings, and extra tags.
    Ref: Features 41, 42, 81, 82
    """
    invoice = models.OneToOneField(SalesInvoice, on_delete=models.CASCADE, related_name='meta')
    
    # Feature 42: Approval System
    status = models.CharField(
        max_length=20, 
        choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
        default='approved', # Default to approved for speed, unless config changes
        help_text="Approval status for printing"
    )
    
    # Feature 81: Home Delivery
    delivery_status = models.CharField(
        max_length=20,
        choices=[('pending', 'Pending'), ('out_for_delivery', 'Out for Delivery'), ('delivered', 'Delivered')],
        default='pending'
    )
    delivery_boy = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='deliveries')
    
    # Feature 82: Bill Tagging & Feature 41: Print Coords (can be part of settings or per bill)
    tags = models.JSONField(default=list, blank=True, help_text="Tags like 'Urgent', 'Morning Delivery'")
    
    # Feature 41: Configurable Invoice (Per invoice settings snapshot?)
    # or just keep generic settings elsewhere. 
    # Storing specific print flags here if needed.
    
    def __str__(self):
        return f"Meta for {self.invoice.invoice_number}"

class InvoiceSettings(models.Model):
    """
    Feature 41: Configurable Invoice.
    Stores print coordinates and template settings.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='invoice_settings')
    
    # Print Coordinates (X, Y offsets in mm or pixels)
    print_offset_x = models.IntegerField(default=0)
    print_offset_y = models.IntegerField(default=0)
    
    # Template
    template_name = models.CharField(max_length=50, default='standard_a4')
    
    # Extra text
    terms_conditions = models.TextField(blank=True)
    header_text = models.TextField(blank=True)
    footer_text = models.TextField(blank=True)
    
    def __str__(self):
        return f"Invoice Settings for {self.user}"

# =============================================================================
# NEW VOUCHER TYPES (Non-Accounting)
# =============================================================================

class SalesOrder(models.Model):
    """
    Feature 4: Order & Challan.
    Step 1: Order received. No Stock impact.
    Feature 78: Order CRM (Kanban stages).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=100)
    date = models.DateField()
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    
    # Feature 78: Stages
    stage = models.CharField(
        max_length=50, 
        default='new', 
        choices=[('new', 'New'), ('packed', 'Packed'), ('shipped', 'Shipped'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]
    )
    
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"SO: {self.order_number} ({self.customer.name})"

class SalesOrderItem(models.Model):
    order = models.ForeignKey(SalesOrder, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

class DeliveryChallan(models.Model):
    """
    Feature 4: Order & Challan.
    Step 2: Goods delivered. Stock REDUCED. No Financial Impact (Ledger).
    Feature 38: Auto-Generate Bill (Convert Challan -> Bill).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    challan_number = models.CharField(max_length=100)
    date = models.DateField()
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.SET_NULL, null=True, blank=True)
    
    is_billed = models.BooleanField(default=False, help_text="True if converted to SalesInvoice")
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DC: {self.challan_number} ({self.customer.name})"

class DeliveryChallanItem(models.Model):
    challan = models.ForeignKey(DeliveryChallan, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    
    # Note: Stock impact happens here. Need signal or method to reduce stock.

class PurchaseIndent(models.Model):
    """
    Feature 9: Purchase Indent.
    "Wishlist" mode. No stock impact.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    description = models.TextField(help_text="Reason for indent e.g. 'Low stock for Diwali'")
    status = models.CharField(max_length=20, default='open', choices=[('open', 'Open'), ('ordered', 'Ordered')])
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

class PurchaseIndentItem(models.Model):
    indent = models.ForeignKey(PurchaseIndent, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    required_quantity = models.PositiveIntegerField()


# =============================================================================
# E-WAY BILL & E-INVOICE (Stubs — requires NIC API credentials for production)
# =============================================================================

class EWayBill(models.Model):
    """Feature 74: E-Way Bill generation stub."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(SalesInvoice, on_delete=models.CASCADE, related_name='eway_bill')
    eway_bill_number = models.CharField(max_length=50, blank=True, help_text="Generated e-way bill number")
    
    vehicle_number = models.CharField(max_length=20, blank=True)
    transporter_name = models.CharField(max_length=100, blank=True)
    transporter_id = models.CharField(max_length=20, blank=True, help_text="GSTIN of transporter")
    distance_km = models.PositiveIntegerField(default=0)
    
    generated_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    
    status = models.CharField(max_length=20, default='draft', choices=[
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('cancelled', 'Cancelled'),
    ])
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"E-Way Bill: {self.eway_bill_number or 'Draft'} for {self.invoice.invoice_number}"


class EInvoice(models.Model):
    """Feature 75: E-Invoice (IRN) generation stub."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(SalesInvoice, on_delete=models.CASCADE, related_name='e_invoice')
    
    irn = models.CharField(max_length=100, blank=True, help_text="Invoice Reference Number from NIC")
    ack_number = models.CharField(max_length=50, blank=True)
    ack_date = models.DateTimeField(null=True, blank=True)
    
    signed_invoice = models.JSONField(default=dict, blank=True, help_text="Signed JSON from NIC")
    qr_code_data = models.TextField(blank=True, help_text="QR code data string")
    
    status = models.CharField(max_length=20, default='pending', choices=[
        ('pending', 'Pending'),
        ('generated', 'Generated'),
        ('cancelled', 'Cancelled'),
    ])
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"E-Invoice IRN: {self.irn or 'Pending'} for {self.invoice.invoice_number}"

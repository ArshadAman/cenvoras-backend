from django.db import models
from django.conf import settings
import uuid

# Import Product from inventory
# Import Product from inventory
from inventory.models import Product, ProductBatch, Warehouse
from cenvoras.constants import IndianStates


class BillPaymentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PARTIAL_PAID = 'partial_paid', 'Partial Paid'
    PAID = 'paid', 'Paid'

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
        max_length=50, 
        blank=True, 
        null=True,
        help_text="Customer's State/Region (Determines Tax Treatment)"
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
        max_length=50, 
        blank=True, 
        null=True,
        help_text="Vendor's State/Region (Determines Tax Treatment)"
    )

    # Compliance & GST Shield fields
    compliance_score = models.IntegerField(default=100, help_text="Vendor GST Compliance Score (0-100)")
    risk_tier = models.CharField(
        max_length=20, 
        choices=[
            ('safe', 'Safe / Trusted'), 
            ('moderate_risk', 'Moderate Risk'), 
            ('defaulter', 'High Risk / Defaulter')
        ],
        default='safe'
    )
    total_billed_itc = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_reconciled_itc = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_at_risk_itc = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_reconciliation_date = models.DateTimeField(null=True, blank=True)
    default_withholding_enabled = models.BooleanField(default=True, help_text="Automatically hold GST on unmatched bills")
    
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
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=BillPaymentStatus.choices, default=BillPaymentStatus.PENDING)

    # GST Shield & GSTR-2B Reconciliation
    gstr2b_status = models.CharField(
        max_length=30,
        choices=[
            ('pending', 'Pending Verification'),
            ('matched', '100% Matched in GSTR-2B'),
            ('probable_match', 'Probable Match (Fuzzy)'),
            ('missing_in_2b', 'Missing in GSTR-2B (At-Risk ITC)'),
            ('disputed_tax', 'Tax Discrepancy'),
            ('manual_override', 'Manually Approved')
        ],
        default='pending'
    )
    gst_withheld_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Amount of GST withheld from vendor")
    is_gst_withheld = models.BooleanField(default=False, help_text="True if GST amount is currently locked/withheld")
    gst_withholding_override = models.BooleanField(default=False, help_text="True if user manually bypassed withholding")
    gst_withholding_notes = models.TextField(blank=True, default='')

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def refresh_payment_status(self, save=True):
        if self.amount_paid <= 0:
            status_value = BillPaymentStatus.PENDING
        elif self.amount_paid < self.total_amount:
            status_value = BillPaymentStatus.PARTIAL_PAID
        else:
            status_value = BillPaymentStatus.PAID
        self.payment_status = status_value
        if save:
            self.save(update_fields=['payment_status'])
        return status_value


class PurchaseOrder(models.Model):
    """A vendor-facing purchase order. Stock is not affected until converted/received."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirmed', 'Confirmed'),
        ('partially_received', 'Partially Received'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po_number = models.CharField(max_length=100, blank=True, null=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, null=True, blank=True)
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField(blank=True, null=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source_sales_order = models.ForeignKey(
        'billing.SalesOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_orders',
        help_text="Original sales order this purchase order was generated from"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PO-{self.po_number or str(self.id)[:8]}"


class PurchaseOrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    batch = models.ForeignKey('inventory.ProductBatch', on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField()
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField(blank=True, default='', help_text="Custom item description / note")
    source_item_id = models.CharField(max_length=64, null=True, blank=True, help_text="ID of source item in SalesOrder")

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"

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
    description = models.TextField(blank=True, default='', help_text="Custom item description / note")

    # Scheme Support (Phase 6)
    free_quantity = models.PositiveIntegerField(default=0, help_text="Qty received free under scheme")


class SalesInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True)
    customer_name = models.CharField(max_length=255, null=True, blank=True)  # Always store customer name as text
    customer_address = models.TextField(blank=True, null=True)
    invoice_number = models.CharField(max_length=100)
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    po_number = models.CharField(max_length=100, blank=True, null=True)
    po_date = models.DateField(null=True, blank=True)
    challan_number = models.CharField(max_length=100, blank=True, null=True)
    challan_date = models.DateField(null=True, blank=True)
    delivery_address = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=[('draft', 'Draft'), ('final', 'Final')], default='final')
    
    # Tax fields
    place_of_supply = models.CharField(
        max_length=50, 
        blank=True, 
        null=True,
        help_text="State/Region code where goods are supplied"
    )
    
    gst_treatment = models.CharField(max_length=50, blank=True, null=True)
    journal = models.CharField(max_length=50, default="Sales")
    round_off = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, help_text="Warehouse from where items are sold")
    source_sales_order = models.ForeignKey(
        'billing.SalesOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales_invoices',
        help_text="Original sales order this invoice was converted from"
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=BillPaymentStatus.choices, default=BillPaymentStatus.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def refresh_payment_status(self, save=True):
        if self.amount_paid <= 0:
            status_value = BillPaymentStatus.PENDING
        elif self.amount_paid < self.total_amount:
            status_value = BillPaymentStatus.PARTIAL_PAID
        else:
            status_value = BillPaymentStatus.PAID
        
        old_status = self.payment_status
        self.payment_status = status_value
        
        if save:
            self.save(update_fields=['payment_status'])
            import sys
            print(f"DEBUG: SalesInvoice {self.pk} refresh_payment_status - "
                  f"amount_paid={self.amount_paid}, total={self.total_amount}, "
                  f"{old_status} → {status_value}", file=sys.stderr)

    def delete(self, *args, **kwargs):
        from billing.models_sidecar import DeliveryChallan, SalesOrder
        from django.db.models import Q

        challan_numbers = [c.strip() for c in (self.challan_number or '').split(',') if c.strip()]
        challans_to_revert = DeliveryChallan.objects.filter(
            Q(converted_invoice=self) |
            (Q(challan_number__in=challan_numbers) & Q(created_by=self.created_by))
        )
        reverted_challans = list(challans_to_revert)
        challans_to_revert.update(
            is_billed=False,
            status='open',
            converted_invoice=None
        )

        # If any reverted challan or invoice was linked to a sales order, reconcile order stage & quantities
        so_ids = {c.sales_order_id for c in reverted_challans if c.sales_order_id}
        if getattr(self, 'sales_order_id', None):
            so_ids.add(self.sales_order_id)
        if self.po_number:
            so_by_po = SalesOrder.objects.filter(order_number=self.po_number, created_by=self.created_by).values_list('id', flat=True)
            so_ids.update(so_by_po)

        if so_ids:
            from billing.sync_service import DocumentSyncService
            sos = list(SalesOrder.objects.filter(id__in=so_ids, created_by=self.created_by))
            if sos:
                DocumentSyncService.reconcile_sales_order_dispatched_state(sos, self.created_by)

        return super().delete(*args, **kwargs)

    class Meta:
        unique_together = [['created_by', 'invoice_number']]
        indexes = [
            models.Index(fields=['created_by', 'invoice_number']),
            models.Index(fields=['created_by', 'invoice_date']),
        ]


class InvoiceSequence(models.Model):
    """
    Thread-safe atomic sequence tracker for multi-user / multi-cashier billing.
    Prevents race conditions and invoice number collisions under concurrent usage.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='invoice_sequences')
    document_type = models.CharField(max_length=50, default='sales_invoice')
    prefix = models.CharField(max_length=50)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['tenant', 'document_type', 'prefix']]
        indexes = [
            models.Index(fields=['tenant', 'document_type', 'prefix']),
        ]

    def __str__(self):
        return f"{self.tenant_id} - {self.document_type} - {self.prefix}{self.last_number}"


class SalesInvoiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sales_invoice = models.ForeignKey(SalesInvoice, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True)
    row_type = models.CharField(max_length=20, default='item', help_text="Type of row: 'item' or 'note'")
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL, null=True, blank=True, help_text="Specific batch being sold")
    hsn_sac_code = models.CharField(max_length=20, blank=True, null=True)
    quantity = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=20, blank=True, null=True)
    price = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    discount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True, default='', help_text="Custom item description / note")
    source_item_id = models.CharField(max_length=64, null=True, blank=True, help_text="ID of source item in DeliveryChallan or SalesOrder")
    
    # Scheme Support (Phase 6)
    free_quantity = models.PositiveIntegerField(default=0, help_text="Qty given free under scheme (Buy X Get Y)")

# Import Sidecar Models to ensure they are registered
from .models_sidecar import TransactionMeta, InvoiceSettings, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, PurchaseIndent, PurchaseIndentItem
from .models_returns import CreditNote, CreditNoteItem, DebitNote, DebitNoteItem
from .models_gst_shield import GSTR2BImport, GSTR2BRecord, VendorLegalNotice

from django.db import models
from django.conf import settings
import uuid


class GSTR2BImport(models.Model):
    """Tracks an ingested GSTR-2B monthly statement file or API sync"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    return_period = models.CharField(max_length=20, help_text="e.g. 082026 or Aug-2026")
    financial_year = models.CharField(max_length=20, default='2026-2027')
    source = models.CharField(
        max_length=20, 
        choices=[('file_upload', 'File Upload'), ('api_sync', 'Govt API Sync')], 
        default='file_upload'
    )
    file_name = models.CharField(max_length=255, blank=True, null=True)
    
    total_invoices = models.PositiveIntegerField(default=0)
    total_taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_itc_available = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_cess = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    matched_count = models.PositiveIntegerField(default=0)
    probable_count = models.PositiveIntegerField(default=0)
    missing_in_books_count = models.PositiveIntegerField(default=0)
    
    reconciled_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        ordering = ['-reconciled_at']

    def __str__(self):
        return f"GSTR-2B {self.return_period} ({self.created_by.email})"


class GSTR2BRecord(models.Model):
    """Individual invoice/credit note record inside GSTR-2B"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    import_batch = models.ForeignKey(GSTR2BImport, related_name='records', on_delete=models.CASCADE)
    
    supplier_gstin = models.CharField(max_length=15, db_index=True)
    supplier_trade_name = models.CharField(max_length=255, blank=True, null=True)
    invoice_number = models.CharField(max_length=100, db_index=True)
    normalized_invoice_number = models.CharField(max_length=100, db_index=True)
    invoice_type = models.CharField(max_length=20, default='B2B', help_text="B2B, B2BA, CDNR, CDNRA, etc.")
    invoice_date = models.DateField(null=True, blank=True)
    
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cess = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    
    itc_availability = models.CharField(max_length=10, default='Y', help_text="'Y' if eligible, 'N' if ineligible under Section 17(5)")
    filing_date = models.DateField(null=True, blank=True)
    
    match_status = models.CharField(
        max_length=30,
        choices=[
            ('matched', 'Matched with Purchase Bill'),
            ('probable', 'Probable Match'),
            ('missing_in_books', 'Present in 2B but Missing in Books'),
            ('tax_mismatch', 'Tax Amount Mismatch')
        ],
        default='missing_in_books'
    )
    matched_purchase_bill = models.ForeignKey(
        'billing.PurchaseBill', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='gstr2b_records'
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        indexes = [
            models.Index(fields=['supplier_gstin', 'normalized_invoice_number']),
            models.Index(fields=['created_by', 'match_status']),
        ]

    def __str__(self):
        return f"{self.supplier_gstin} - {self.invoice_number} ({self.taxable_value})"


class VendorLegalNotice(models.Model):
    """Tracks statutory legal demand notices sent to delinquent suppliers"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey('billing.Vendor', on_delete=models.CASCADE, related_name='legal_notices')
    notice_number = models.CharField(max_length=100, unique=True)
    generated_date = models.DateField(auto_now_add=True)
    period = models.CharField(max_length=50, help_text="e.g. Q1 2026 or Aug-2026")
    
    total_unmatched_invoices = models.PositiveIntegerField(default=1)
    total_invoiced_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_itc_claimed_back = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    statutory_deadline_date = models.DateField(help_text="Deadline for vendor to file (e.g. 7 days from notice)")
    
    invoices_payload = models.JSONField(default=list, help_text="List of invoice details included in notice")
    notice_text = models.TextField(blank=True, default='')
    
    status = models.CharField(
        max_length=30,
        choices=[
            ('draft', 'Draft Notice'),
            ('generated', 'Generated PDF'),
            ('sent_whatsapp', 'Sent via WhatsApp'),
            ('sent_email', 'Sent via Email'),
            ('resolved', 'Resolved / Vendor Filed'),
            ('disputed', 'Disputed')
        ],
        default='generated'
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Legal Notice {self.notice_number} - {self.vendor.name}"

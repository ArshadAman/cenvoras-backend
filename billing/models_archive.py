"""
Read-Only Historical Document Archive (billing/models_archive.py).
Stores 1-3 years of migrated Tally/Marg historical invoices, bills, and vouchers
so users can search and reprint past bills without opening legacy desktop software.
"""
import uuid
from django.conf import settings
from django.db import models


class HistoricalDocumentArchive(models.Model):
    SOURCE_CHOICES = [
        ('tally', 'TallyPrime'),
        ('marg', 'Marg ERP 9+'),
        ('excel', 'Excel / CSV'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='historical_archives',
    )
    source_erp = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='tally')
    voucher_type = models.CharField(max_length=40, db_index=True, help_text="Sales, Purchase, Credit Note, Debit Note, etc.")
    document_number = models.CharField(max_length=100, db_index=True)
    document_date = models.DateField(null=True, blank=True, db_index=True)
    party_name = models.CharField(max_length=255, blank=True, default='', db_index=True)
    party_gstin = models.CharField(max_length=20, blank=True, default='')
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    items = models.JSONField(default=list, blank=True, help_text="Archived line items [{item_name, quantity, rate, amount}]")
    raw_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-document_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'document_date'], name='idx_hist_tenant_date'),
            models.Index(fields=['tenant', 'document_number'], name='idx_hist_tenant_docno'),
            models.Index(fields=['tenant', 'party_name'], name='idx_hist_tenant_party'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'source_erp', 'voucher_type', 'document_number'],
                name='uniq_hist_doc_per_tenant_source',
                condition=~models.Q(document_number=''),
            )
        ]

    def __str__(self):
        return f"[{self.source_erp.upper()}] {self.voucher_type} {self.document_number} - {self.party_name}"

import uuid
from django.db import models
from django.conf import settings

class AccountType(models.TextChoices):
    ASSET = 'asset', 'Asset'
    LIABILITY = 'liability', 'Liability'
    EQUITY = 'equity', 'Equity'
    REVENUE = 'revenue', 'Revenue'
    EXPENSE = 'expense', 'Expense'

class Account(models.Model):
    """Chart of Accounts for double-entry accounting"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, blank=True)  # Account code like 1001, 2001, etc.
    name = models.CharField(max_length=100)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    parent_account = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = [['name', 'created_by'], ['code', 'created_by']]
        ordering = ['account_type', 'code', 'name']
    
    def __str__(self):
        return f"{self.code} - {self.name}" if self.code else self.name

class GeneralLedgerEntry(models.Model):
    """General ledger entries for double-entry accounting"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='ledger_entries')
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField()
    reference = models.CharField(max_length=100, blank=True)  # Invoice number, receipt number, etc.
    # Link to source documents
    sales_invoice = models.ForeignKey('billing.SalesInvoice', on_delete=models.CASCADE, null=True, blank=True)
    purchase_bill = models.ForeignKey('billing.PurchaseBill', on_delete=models.CASCADE, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date', '-created_at']
    
    def __str__(self):
        return f"{self.account.name} - Dr:{self.debit} Cr:{self.credit}"

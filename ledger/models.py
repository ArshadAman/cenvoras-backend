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

class BankStatement(models.Model):
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=50, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    file_name = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.bank_name} - {self.uploaded_at.date()}"

class BankStatementLine(models.Model):
    statement = models.ForeignKey(BankStatement, related_name='lines', on_delete=models.CASCADE)
    date = models.DateField()
    description = models.CharField(max_length=500)
    reference_no = models.CharField(max_length=100, blank=True)
    debit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    # Reconciliation Status
    is_reconciled = models.BooleanField(default=False)
    matched_entry = models.ForeignKey(GeneralLedgerEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name='bank_matches')
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date} - {self.description} ({self.credit_amount - self.debit_amount})"

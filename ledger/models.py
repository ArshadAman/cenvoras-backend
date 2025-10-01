import uuid
from django.db import models
from django.conf import settings
from billing.models import Customer, SalesInvoice

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

class JournalEntry(models.Model):
    """Journal entries for double-entry accounting"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    description = models.TextField()
    reference = models.CharField(max_length=100, blank=True)  # Invoice number, receipt number, etc.
    # Link to source documents
    sales_invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, null=True, blank=True)
    purchase_bill = models.ForeignKey('billing.PurchaseBill', on_delete=models.CASCADE, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date', '-created_at']
    
    def __str__(self):
        return f"{self.date} - {self.description}"
    
    @property
    def total_debits(self):
        return sum(entry.debit for entry in self.ledger_entries.all())
    
    @property
    def total_credits(self):
        return sum(entry.credit for entry in self.ledger_entries.all())
    
    @property
    def is_balanced(self):
        return self.total_debits == self.total_credits

class GeneralLedgerEntry(models.Model):
    """Individual ledger entries for each account (part of journal entries)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='ledger_entries')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='ledger_entries')
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-journal_entry__date', '-created_at']
    
    def __str__(self):
        return f"{self.account.name} - Dr:{self.debit} Cr:{self.credit}"

class ClientLedgerEntry(models.Model):
    """Subsidiary ledger for customer accounts (Accounts Receivable detail)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='ledger_entries')
    date = models.DateField()
    description = models.CharField(max_length=255)
    invoice = models.ForeignKey(SalesInvoice, null=True, blank=True, on_delete=models.SET_NULL)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name='client_ledger_entries')
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)   # Sales (amount owed by client)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # Payments received
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0) # Running balance
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'created_at']

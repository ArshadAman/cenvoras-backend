import uuid
from django.db import models
from django.conf import settings
from billing.models import Customer, SalesInvoice

class ClientLedgerEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='ledger_entries')
    date = models.DateField()
    description = models.CharField(max_length=255)
    invoice = models.ForeignKey(SalesInvoice, null=True, blank=True, on_delete=models.SET_NULL)
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)   # Sales (amount owed by client)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # Payments received
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0) # Running balance
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'created_at']

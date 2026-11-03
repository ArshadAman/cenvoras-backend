"""
RCM documents (Rudra CA follow-up to UAT P1 #7), for supplies taxed under reverse charge.

* RcmSelfInvoice - s.31(3)(f) / Rule 47A: a registered person liable to pay tax under RCM on a
  supply from an UNREGISTERED supplier issues an invoice to itself within 30 days of receiving
  the supply. Own series (RCM-YYYY-NNNN, <=16 chars per Rule 46).
* RcmPaymentVoucher - s.31(3)(g): a payment voucher issued when paying the supplier of an RCM
  supply, created and dated from each payment entry (two instalments = two vouchers).
  Own series (RPV-YYYY-NNNN).
Both are additive tables; nothing existing changes.
"""
import uuid

from django.conf import settings
from django.db import models


class RcmSelfInvoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='rcm_self_invoices')
    purchase_bill = models.OneToOneField('billing.PurchaseBill', on_delete=models.PROTECT,
                                         related_name='rcm_self_invoice')
    number = models.CharField(max_length=16)
    date = models.DateField()
    receipt_date = models.DateField(help_text='Date the supply was received (Rule 47A: self-invoice within 30 days)')
    supplier_name = models.CharField(max_length=255)
    supplier_address = models.TextField(blank=True, default='')
    supplier_state = models.CharField(max_length=100, blank=True, default='')
    place_of_supply = models.CharField(max_length=100, blank=True, default='')
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('created_by', 'number')]
        ordering = ['-date', '-number']

    def __str__(self):
        return self.number


class RcmPaymentVoucher(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name='rcm_payment_vouchers')
    purchase_bill = models.ForeignKey('billing.PurchaseBill', on_delete=models.PROTECT,
                                      related_name='rcm_payment_vouchers')
    number = models.CharField(max_length=16)
    date = models.DateField()
    supplier_name = models.CharField(max_length=255)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2)
    payment_mode = models.CharField(max_length=30, blank=True, default='')
    payment_reference = models.CharField(max_length=100, blank=True, default='')
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('created_by', 'number')]
        ordering = ['-date', '-number']

    def __str__(self):
        return self.number

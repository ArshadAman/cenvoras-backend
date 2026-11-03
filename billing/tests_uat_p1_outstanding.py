"""UAT P1 item 2: outstanding = opening + invoices + DNs - receipts - CNs (drafts excluded)."""
from datetime import date
from decimal import Decimal

from billing.balance_sync import (customer_balance_breakdown, customer_outstanding,
                                  recompute_customer_balance, total_receivables)
from billing.models import Customer, Payment, SalesInvoice, SalesInvoiceItem
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase


class OutstandingFormulaTests(FourFixesBase):
    def _inv(self, num, total, status='final', d=date(2026, 10, 10), taxable=None):
        inv = SalesInvoice.objects.create(customer=self.customer, customer_name=self.customer.name,
                                          invoice_number=num, invoice_date=d, status=status,
                                          total_amount=Decimal(total), created_by=self.tenant)
        if taxable is not None:
            SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, hsn_sac_code='847130',
                                            quantity=1, price=Decimal(taxable), tax=Decimal('18'),
                                            amount=Decimal(total))
        return inv

    def _cn(self, num, total, inv=None, d=date(2026, 10, 20), taxable=None):
        cn = CreditNote.objects.create(credit_note_number=num, date=d, customer=self.customer,
                                       original_invoice=inv, reason='discount',
                                       total_amount=Decimal(total), created_by=self.tenant)
        if taxable is not None:
            CreditNoteItem.objects.create(credit_note=cn, product=self.product, quantity=1,
                                          price=Decimal(taxable), tax=Decimal('18'), amount=Decimal(total),
                                          hsn_sac_code='847130')
        return cn

    def test_outstanding_is_invoices_minus_receipts_minus_cns_excluding_drafts(self):
        inv = self._inv('I-1', '3839.10')
        self._inv('I-DRAFT', '5000.00', status='draft')
        Payment.objects.create(customer=self.customer, invoice=inv, date=date(2026, 10, 12),
                               amount=Decimal('500.00'), created_by=self.tenant)
        self._cn('CN-1', '445.00', inv=inv)
        self._cn('CN-2', '500.00')  # unlinked = on-account credit
        row = customer_balance_breakdown(self.tenant)[self.customer.id]
        self.assertEqual(row['invoices'], Decimal('3839.10'))
        self.assertEqual(row['receipts'], Decimal('500.00'))
        self.assertEqual(row['credit_notes'], Decimal('945.00'))
        self.assertEqual(row['outstanding'], Decimal('2394.10'))
        self.assertEqual(row['unallocated_credit'], Decimal('500.00'))
        self.assertEqual(customer_outstanding(self.customer), Decimal('2394.10'))
        self.assertEqual(total_receivables(self.tenant), Decimal('2394.10'))

    def test_stored_balance_follows_credit_notes(self):
        self._inv('I-1', '1000.00')
        recompute_customer_balance(self.customer.id)
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).current_balance, Decimal('1000.00'))
        with self.captureOnCommitCallbacks(execute=True):
            cn = self._cn('CN-1', '200.00')
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).current_balance, Decimal('800.00'))
        with self.captureOnCommitCallbacks(execute=True):
            cn.delete()
        self.assertEqual(Customer.objects.get(pk=self.customer.pk).current_balance, Decimal('1000.00'))

    def test_receipt_on_draft_invoice_ignored(self):
        draft = self._inv('I-D', '700.00', status='draft')
        Payment.objects.create(customer=self.customer, invoice=draft, date=date(2026, 10, 12),
                               amount=Decimal('100.00'), created_by=self.tenant)
        self.assertEqual(customer_outstanding(self.customer), Decimal('0.00'))

    def test_dashboard_receivables_uses_formula(self):
        from analytics.smart_dashboard import SmartDashboard
        self._inv('I-1', '1000.00')
        self._cn('CN-1', '250.00')
        dash = SmartDashboard.__new__(SmartDashboard)
        dash.owner = self.tenant
        self.assertEqual(dash._get_total_receivables(), 750.0)


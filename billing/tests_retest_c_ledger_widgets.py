"""Retest item 2 leftovers: overdue widget, reconciliation and Net Balance use the ledger formula."""
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APIClient

from billing.models import Payment
from billing.tests_four_fixes import FourFixesBase
from billing.tests_uat_p1_outstanding import OutstandingFormulaTests


class LedgerWidgetTests(FourFixesBase):
    _inv = OutstandingFormulaTests._inv
    _cn = OutstandingFormulaTests._cn

    def setUp(self):
        super().setUp()
        cache.clear()
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.tenant)
        # Anita-like: INV 1157.99, linked CN 945 -> 212.99 outstanding, overdue.
        self.inv = self._inv('INV-003', '1157.99', d=date(2026, 9, 1))
        self._cn('CN-0001', '945.00', inv=self.inv, d=date(2026, 9, 5))

    def test_overdue_widget_nets_linked_cn_and_receipts(self):
        Payment.objects.create(customer=self.customer, invoice=self.inv, date=date(2026, 9, 6),
                               amount=Decimal('12.99'), created_by=self.tenant)
        r = self.client_api.get('/api/billing/reports/overdue-bills/').json()
        row = [x for x in r['results'] if x['invoice_number'] == 'INV-003'][0]
        self.assertEqual(Decimal(str(row['outstanding_amount'])), Decimal('200.00'))

    def test_overdue_widget_drops_invoice_fully_credited(self):
        self._cn('CN-0002', '212.99', inv=self.inv, d=date(2026, 9, 6))
        r = self.client_api.get('/api/billing/reports/overdue-bills/').json()
        self.assertFalse([x for x in r['results'] if x['invoice_number'] == 'INV-003'])

    def test_reconciliation_does_not_flag_linked_cn(self):
        r = self.client_api.get('/api/billing/reports/customer-balance-reconciliation/').json()
        row = [x for x in r['results'] if x['customer_id'] == str(self.customer.id)][0]
        self.assertEqual(Decimal(str(row['difference'])), Decimal('0'))
        self.assertEqual(Decimal(str(row['invoice_outstanding'])), Decimal('212.99'))

    def test_net_balance_is_ledger_outstanding(self):
        r = self.client_api.get('/api/ledger/accounts/stats/', {'customer': str(self.customer.id)}).json()
        self.assertEqual(Decimal(str(r['net_balance'])), Decimal('212.99'))
        r = self.client_api.get('/api/ledger/accounts/stats/').json()
        self.assertEqual(Decimal(str(r['net_balance'])), Decimal('212.99'))
        self.assertEqual(Decimal(str(r['unapplied_credits'])), Decimal('0'))

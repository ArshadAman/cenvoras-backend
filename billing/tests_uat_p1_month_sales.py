"""UAT P1 item 3: sales for a named month (IST month bounds, taxable, net of CNs)."""
from datetime import date
from decimal import Decimal

from billing.models import SalesInvoice, SalesInvoiceItem
from billing.models_returns import CreditNote, CreditNoteItem
from billing.sales_metrics import (answer_month_sales_question, ist_month_bounds,
                                   resolve_month_reference, sales_for_month)
from billing.tests_four_fixes import FourFixesBase


class MonthSalesTests(FourFixesBase):
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

    def test_ist_month_bounds_are_one_based(self):
        self.assertEqual(ist_month_bounds(2026, 10), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(ist_month_bounds(2024, 2), (date(2024, 2, 1), date(2024, 2, 29)))
        with self.assertRaises(ValueError):
            ist_month_bounds(2026, 0)

    def test_month_name_resolution(self):
        today = date(2026, 11, 3)
        self.assertEqual(resolve_month_reference('October sales', today), (2026, 10))
        self.assertEqual(resolve_month_reference('sales in oct', today), (2026, 10))
        self.assertEqual(resolve_month_reference('December sales', today), (2025, 12))
        self.assertEqual(resolve_month_reference('sales for March 2025', today), (2025, 3))
        self.assertEqual(resolve_month_reference('November sales', today), (2026, 11))
        self.assertIsNone(resolve_month_reference('how are sales today', today))

    def test_sales_for_month_taxable_net_of_cns_and_boundaries(self):
        self._inv('I-SEP', '118.00', d=date(2026, 9, 30), taxable='100')
        self._inv('I-OCT1', '1180.00', d=date(2026, 10, 1), taxable='1000')
        self._inv('I-OCT31', '590.00', d=date(2026, 10, 31), taxable='500')
        self._inv('I-NOV', '236.00', d=date(2026, 11, 1), taxable='200')
        self._inv('I-DRAFT', '1180.00', d=date(2026, 10, 15), status='draft', taxable='1000')
        self._cn('CN-OCT', '118.00', d=date(2026, 10, 20), taxable='100')
        r = sales_for_month(self.tenant, 2026, 10)
        self.assertEqual(r['invoice_taxable'], Decimal('1500.00'))
        self.assertEqual(r['credit_note_taxable'], Decimal('100.00'))
        self.assertEqual(r['sales'], Decimal('1400.00'))
        ans = answer_month_sales_question('What were my October sales?', self.tenant, date(2026, 11, 3))
        self.assertIn('October 2026', ans)
        self.assertIn('1,400.00', ans)
        self.assertIsNone(answer_month_sales_question('top product?', self.tenant, date(2026, 11, 3)))

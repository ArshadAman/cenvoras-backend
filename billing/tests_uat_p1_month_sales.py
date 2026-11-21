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
        oct1 = self._inv('I-OCT1', '1180.00', d=date(2026, 10, 1), taxable='1000')
        self._inv('I-OCT31', '590.00', d=date(2026, 10, 31), taxable='500')
        self._inv('I-NOV', '236.00', d=date(2026, 11, 1), taxable='200')
        self._inv('I-DRAFT', '1180.00', d=date(2026, 10, 15), status='draft', taxable='1000')
        self._cn('CN-OCT', '118.00', oct1, d=date(2026, 10, 20), taxable='100')
        r = sales_for_month(self.tenant, 2026, 10)
        self.assertEqual(r['invoice_taxable'], Decimal('1500.00'))
        self.assertEqual(r['credit_note_taxable'], Decimal('100.00'))
        self.assertEqual(r['sales'], Decimal('1400.00'))
        ans = answer_month_sales_question('What were my October sales?', self.tenant, date(2026, 11, 3))
        self.assertIn('October 2026', ans)
        self.assertIn('1,400.00', ans)
        self.assertIsNone(answer_month_sales_question('top product?', self.tenant, date(2026, 11, 3)))


class MonthSalesMatchesRegisterTests(FourFixesBase):
    _inv = MonthSalesTests._inv
    _cn = MonthSalesTests._cn

    def test_ai_month_sales_equals_tax_register(self):
        from rest_framework.test import APIClient
        oct_inv = self._inv('I-A', '1180.00', d=date(2026, 10, 5), taxable='1000')
        sep_inv = self._inv('I-B', '590.00', d=date(2026, 9, 25), taxable='500')
        draft = self._inv('I-D', '236.00', d=date(2026, 10, 6), status='draft', taxable='200')
        self._cn('CN-L1', '118.00', oct_inv, d=date(2026, 10, 10), taxable='100')   # linked, same month
        self._cn('CN-L2', '59.00', sep_inv, d=date(2026, 10, 12), taxable='50')     # linked to Sept invoice, dated Oct
        self._cn('CN-L3', '23.60', oct_inv, d=date(2026, 11, 2), taxable='20')      # dated Nov: excluded
        self._cn('CN-U', '236.00', None, d=date(2026, 10, 15), taxable='200')       # unlinked: not in register
        self._cn('CN-DR', '23.60', draft, d=date(2026, 10, 16), taxable='20')       # on a draft invoice
        r = sales_for_month(self.tenant, 2026, 10)
        c = APIClient(); c.force_authenticate(self.tenant)
        resp = c.get('/api/billing/gst/tax-register/', {'from': '2026-10-01', 'to': '2026-10-31', 'type': 'sales'})
        if resp.status_code == 404:
            from django.urls import reverse
            resp = c.get(reverse('tax_register'), {'from': '2026-10-01', 'to': '2026-10-31', 'type': 'sales'})
        self.assertEqual(resp.status_code, 200)
        reg = resp.json()
        self.assertEqual(Decimal(str(reg['totals']['taxable'])).quantize(Decimal('0.01')), r['gst_taxable_turnover'])
        self.assertEqual(r['gst_taxable_turnover'], Decimal('850.00'))
        # Books: every CN dated in Oct (100 + 50 + 200 unlinked + 20 on draft invoice)
        self.assertEqual(r['net_sales_books'], Decimal('630.00'))
        cn_rows = [x for x in reg['results'] if x.get('doc_type') == 'credit_note']
        self.assertEqual(len(cn_rows), r['gst_credit_note_count'])
        self.assertEqual(r['gst_credit_note_count'], 2)
        self.assertEqual(r['credit_note_count'], 4)


class NetSalesBooksVsGstTests(FourFixesBase):
    """Rudra ruling post-#221: books subtract ALL CNs; GST tables only linked, non-time-barred."""
    _inv = MonthSalesTests._inv

    def _cn2(self, num, total, inv, taxable, tax, barred=False):
        cn = CreditNote.objects.create(credit_note_number=num, date=date(2026, 10, 20), customer=self.customer,
                                       original_invoice=inv, reason='discount', gst_time_barred=barred,
                                       total_amount=Decimal(total), created_by=self.tenant)
        CreditNoteItem.objects.create(credit_note=cn, product=self.product, quantity=1, price=Decimal(taxable),
                                      tax=Decimal(tax), amount=Decimal(total), hsn_sac_code='847130')
        return cn

    def test_two_figures_and_gst_tables(self):
        from rest_framework.test import APIClient
        from billing.gstr1_compute import compute_gstr1
        inv = self._inv('I-1', '1180.00', d=date(2026, 10, 5), taxable='1000')
        self._inv('I-DR', '590.00', d=date(2026, 10, 6), status='draft', taxable='500')
        self._cn2('CN-LINK', '118.00', inv, '100', '18')            # linked, adjusts GST
        self._cn2('CN-NOGST', '50.00', None, '50', '0', barred=True)  # unlinked without GST
        self._cn2('CN-TB', '30.00', inv, '30', '0', barred=True)      # linked but time-barred
        r = sales_for_month(self.tenant, 2026, 10)
        self.assertEqual(r['invoice_taxable'], Decimal('1000.00'))
        self.assertEqual(r['net_sales_books'], Decimal('820.00'))
        self.assertEqual(r['gst_taxable_turnover'], Decimal('900.00'))
        g1 = compute_gstr1(self.tenant, '2026-10-01', '2026-10-31')
        nums = [n.get('nt_num') for row in g1.get('cdnr', []) for n in row.get('nt', [])]
        nums += [n.get('nt_num') for n in g1.get('cdnur', [])]
        self.assertNotIn('CN-TB', nums)
        self.assertNotIn('CN-NOGST', nums)
        self.assertEqual(Decimal(str(g1['table_3_1_a']['txval'])).quantize(Decimal('0.01')), Decimal('900.00'))
        c = APIClient(); c.force_authenticate(self.tenant)
        from django.urls import reverse
        reg = c.get(reverse('tax_register'), {'from': '2026-10-01', 'to': '2026-10-31', 'type': 'sales'}).json()
        self.assertEqual(Decimal(str(reg['totals']['taxable'])).quantize(Decimal('0.01')), Decimal('900.00'))
        ans = answer_month_sales_question('October sales', self.tenant, date(2026, 11, 3))
        self.assertIn('Net sales (books)', ans)
        self.assertIn('820.00', ans)
        self.assertIn('GST taxable turnover: ₹900.00', ans)

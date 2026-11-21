"""UAT P1 item 5: quantity precision by UQC; never rounded silently; can't exceed sold - returned."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from billing.models import SalesInvoice, SalesInvoiceItem
from billing.tests_four_fixes import FourFixesBase
from billing.uqc import is_measured, quantity_error, to_uqc
from inventory.models import Product


class UqcRuleTests(SimpleTestCase):
    def test_aliases(self):
        self.assertEqual(to_uqc('pcs'), 'PCS')
        self.assertEqual(to_uqc(' Kg '), 'KGS')
        self.assertEqual(to_uqc('ml'), 'MLT')
        self.assertEqual(to_uqc('dozen'), 'DOZ')
        self.assertIsNone(to_uqc('pack'))

    def test_count_units_whole_only(self):
        for u in ('NOS', 'PCS', 'UNT', 'BOX', 'SET', 'PRS', 'DOZ', 'pcs', 'pack', ''):
            self.assertIsNone(quantity_error(Decimal('2'), u), u)
            self.assertIn('whole number', quantity_error(Decimal('1.5'), u), u)

    def test_measured_units_three_decimals(self):
        for u in ('KGS', 'GMS', 'LTR', 'MLT', 'MTR', 'CMS', 'SQM', 'CBM', 'TON', 'QTL', 'kg', 'l'):
            self.assertTrue(is_measured(u), u)
            self.assertIsNone(quantity_error(Decimal('1.5'), u), u)
            self.assertIsNone(quantity_error(Decimal('1.255'), u), u)
            self.assertIn('3 decimals', quantity_error(Decimal('1.2555'), u), u)


class CreditNoteUqcTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.rice = Product.objects.create(name='Rice', hsn_sac_code='100630', sale_price=Decimal('50'),
                                           tax=Decimal('5'), unit='kg', stock=100, created_by=self.tenant)
        self.inv = SalesInvoice.objects.create(customer=self.customer, customer_name='KA Buyer', invoice_number='Q-1',
                                               invoice_date=date.today() - timedelta(days=2),
                                               total_amount=Decimal('2460'), created_by=self.tenant)
        SalesInvoiceItem.objects.create(sales_invoice=self.inv, product=self.product, hsn_sac_code='847130',
                                        quantity=2, price=Decimal('1000'), tax=Decimal('18'), amount=Decimal('2360'))
        SalesInvoiceItem.objects.create(sales_invoice=self.inv, product=self.rice, hsn_sac_code='100630', unit='kg',
                                        quantity=2, price=Decimal('50'), tax=Decimal('5'), amount=Decimal('105'))

    def _post(self, product, qty, reason='discount', price='1000.00', tax='18.00'):
        return self.client.post('/api/billing/credit-notes/', {
            'original_invoice': str(self.inv.id), 'customer': str(self.customer.id), 'reason': reason,
            'total_amount': '0',
            'items': [{'product': str(product.id), 'quantity': qty, 'price': price, 'tax': tax, 'amount': '0',
                       'unit': product.unit}]}, format='json')

    def test_count_unit_fraction_rejected_not_rounded(self):
        res = self._post(self.product, '1.5')
        self.assertEqual(res.status_code, 400)
        self.assertIn('whole number', str(res.data))

    def test_measured_unit_three_decimals_kept(self):
        res = self._post(self.rice, '1.255', price='50.00', tax='5.00')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Decimal(str(res.data['items'][0]['quantity'])), Decimal('1.255'))
        self.assertIn('3 decimal', str(self._post(self.rice, '0.1234', price='50.00', tax='5.00').data))

    def test_cannot_exceed_sold_minus_returned(self):
        self.assertEqual(self._post(self.rice, '1.5', price='50.00', tax='0.00').status_code, 201)
        res = self._post(self.rice, '0.6', price='50.00', tax='0.00')
        self.assertEqual(res.status_code, 400)
        self.assertIn('exceeds remaining quantity', str(res.data))
        self.assertEqual(self._post(self.rice, '0.5', price='50.00', tax='0.00').status_code, 201)

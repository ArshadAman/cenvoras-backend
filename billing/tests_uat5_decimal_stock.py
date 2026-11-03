"""UAT item 5 retest (Lovelish): stock is decimal(15,3), so a 1.5 kg return goes back into stock.
Also: UQC everywhere, return cap, fractional inventory value, and the stock-journal list-cache bump (item 3)."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache

from billing.models import SalesInvoice, SalesInvoiceItem
from billing.tests_four_fixes import FourFixesBase
from inventory.models import Product, ProductBatch, StockPoint, Warehouse
from reports.services import get_stock_valuation

D = Decimal


class DecimalStockTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.wh = Warehouse.objects.create(name='Main', created_by=self.tenant, is_active=True)
        self.rice = Product.objects.create(name='QA KGS Rice', hsn_sac_code='100630', sale_price=D('50'),
                                           price=D('40'), tax=D('5'), unit='KGS', stock=0, created_by=self.tenant)
        self.batch = ProductBatch.objects.create(product=self.rice, batch_number='R1', cost_price=D('40'))
        StockPoint.objects.create(batch=self.batch, warehouse=self.wh, quantity=D('10'))
        self.rice.recalculate_stock()
        self.inv = SalesInvoice.objects.create(customer=self.customer, customer_name='KA Buyer',
                                               invoice_number='INV-017', invoice_date=date.today() - timedelta(days=2),
                                               total_amount=D('157.50'), created_by=self.tenant)
        SalesInvoiceItem.objects.create(sales_invoice=self.inv, product=self.rice, batch=self.batch,
                                        hsn_sac_code='100630', unit='KGS', quantity=D('3'), price=D('50'),
                                        tax=D('5'), amount=D('157.50'))
        StockPoint.objects.filter(batch=self.batch).update(quantity=D('7'))
        self.rice.recalculate_stock()

    def cn(self, qty, product=None, reason='return'):
        product = product or self.rice
        return self.client.post('/api/billing/credit-notes/', {
            'original_invoice': str(self.inv.id), 'customer': str(self.customer.id), 'reason': reason,
            'warehouse': str(self.wh.id), 'total_amount': '0',
            'items': [{'product': str(product.id), 'batch': str(self.batch.id), 'quantity': qty, 'price': '50.00',
                       'tax': '5.00', 'amount': '0', 'unit': product.unit}]}, format='json')

    def test_existing_integer_values_are_exact_decimals(self):
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, D('7.000'))
        self.assertEqual(SalesInvoiceItem.objects.get(sales_invoice=self.inv).quantity, D('3.000'))

    def test_one_and_half_kg_return_saves_and_restores_stock(self):
        res = self.cn('1.5')
        self.assertEqual(res.status_code, 201, res.data)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, D('8.500'))
        self.assertEqual(StockPoint.objects.get(batch=self.batch, warehouse=self.wh).quantity, D('8.500'))

    def test_return_cannot_exceed_sold_minus_earlier_returns(self):
        self.assertEqual(self.cn('1.5').status_code, 201)
        res = self.cn('1.501')
        self.assertEqual(res.status_code, 400)
        self.assertIn('exceeds remaining quantity', str(res.data))
        self.assertEqual(self.cn('1.5').status_code, 201)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, D('10.000'))

    def test_decimal_on_pcs_rejected_everywhere(self):
        pcs = Product.objects.create(name='Widget pcs', hsn_sac_code='847130', sale_price=D('10'), unit='pcs',
                                     stock=5, created_by=self.tenant)
        res = self.client.post('/api/inventory/stock-journals/', {
            'date': str(date.today()), 'warehouse': str(self.wh.id), 'adjustment_type': 'excess',
            'items': [{'product': str(pcs.id), 'quantity': '1.5'}]}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('whole number', str(res.data))
        res = self.client.post('/api/inventory/stock-adjustments/', {
            'product_id': str(pcs.id), 'adjustment_type': 'add', 'quantity': '0.5'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('whole number', str(res.data))
        pcs.refresh_from_db()
        self.assertEqual(pcs.stock, D('5'))  # nothing rounded or applied

    def test_kg_journal_keeps_three_decimals(self):
        res = self.client.post('/api/inventory/stock-journals/', {
            'date': str(date.today()), 'warehouse': str(self.wh.id), 'adjustment_type': 'excess',
            'items': [{'product': str(self.rice.id), 'batch': str(self.batch.id), 'quantity': '0.255'}]},
            format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, D('7.255'))
        res = self.client.post('/api/inventory/stock-journals/', {
            'date': str(date.today()), 'warehouse': str(self.wh.id), 'adjustment_type': 'excess',
            'items': [{'product': str(self.rice.id), 'quantity': '0.2555'}]}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_inventory_value_with_fractional_stock(self):
        StockPoint.objects.filter(batch=self.batch).update(quantity=D('2.505'))
        self.rice.recalculate_stock()
        cache.clear()
        row = next(r for r in get_stock_valuation(self.tenant)['items'] if r['id'] == self.rice.id)
        self.assertEqual(row['stock'], D('2.505'))
        self.assertEqual(row['avg_cost'], D('40.00'))
        self.assertEqual(row['total_value'], D('100.20'))  # 2.505 × 40.00 exactly
        self.assertIsInstance(row['total_value'], Decimal)

    def _list_stock(self):
        res = self.client.get('/api/inventory/products/')
        data = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        return D(str(next(p for p in data if str(p['id']) == str(self.rice.id))['stock']))

    def test_product_list_total_updates_after_stock_journal(self):
        self.assertEqual(self._list_stock(), D('7'))  # cached for 5 minutes
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/inventory/stock-journals/', {
                'date': str(date.today()), 'warehouse': str(self.wh.id), 'adjustment_type': 'excess',
                'items': [{'product': str(self.rice.id), 'batch': str(self.batch.id), 'quantity': '45'}]},
                format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._list_stock(), D('52'))

    def test_api_quantities_stay_numbers(self):
        res = self.cn('1.5')
        self.assertEqual(res.status_code, 201, res.data)
        res = self.client.get(f'/api/inventory/products/{self.rice.id}/')
        self.assertIsInstance(res.data['stock'], (int, float, Decimal))

    def test_unlinked_cn_api_routes(self):
        """Item 12 question: an unlinked CN can only be created through the API."""
        body = {'customer': str(self.customer.id), 'reason': 'discount', 'total_amount': '0',
                'items': [{'product': str(self.rice.id), 'quantity': '1', 'price': '50.00', 'tax': '5.00',
                           'amount': '0', 'unit': 'KGS'}]}
        res = self.client.post('/api/billing/credit-notes/', body, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Link this credit note to the invoice(s) it adjusts', str(res.data))
        res = self.client.post('/api/billing/credit-notes/', dict(body, without_gst=True), format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['gst_time_barred'])
        self.assertEqual(D(str(res.data['total_amount'])), D('50.00'))

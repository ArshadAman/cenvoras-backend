"""UAT P1 #15: same-GSTIN stock transfer — challan at WAC, no GST, stock cap, e-way warning."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from billing.gstr1_compute import compute_gstr1
from inventory.models import Product, ProductBatch, StockPoint, StockTransfer, Warehouse
from inventory.transfer_service import EWAY_WARNING, INTER_GSTIN_NOT_SUPPORTED, fy_label, next_challan_number, product_wac
from ledger.models import GeneralLedgerEntry
from subscription.models import Plan, TenantSubscription
from users.models import User

URL = '/api/inventory/transfers/'


class StockTransferUAT15Tests(TestCase):
    def setUp(self):
        self.t = User.objects.create_user(username='st15', email='st15@x.in', password='pw', role='admin',
                                          state='Odisha', gstin='21ABCDE1234F1Z5', business_name='UAT')
        plan, _ = Plan.objects.get_or_create(code='pro', defaults={'name': 'Pro', 'monthly_price': 0})
        TenantSubscription.objects.create(tenant=self.t, plan=plan, status='active',
                                          current_period_end=date.today() + timedelta(days=365))
        self.c = APIClient()
        self.c.force_authenticate(self.t)
        self.a = Warehouse.objects.create(name='A', created_by=self.t)
        self.b = Warehouse.objects.create(name='B', created_by=self.t)
        self.p = Product.objects.create(name='Widget', hsn_sac_code='847130', price=Decimal('90'),
                                        tax=Decimal('18'), unit='pcs', stock=0, created_by=self.t)
        self.b1 = ProductBatch.objects.create(product=self.p, batch_number='B1', cost_price=Decimal('100'))
        self.b2 = ProductBatch.objects.create(product=self.p, batch_number='B2', cost_price=Decimal('130'))
        StockPoint.objects.create(warehouse=self.a, batch=self.b1, quantity=10)
        StockPoint.objects.create(warehouse=self.a, batch=self.b2, quantity=5)

    def post(self, qty, batch=None, dest=None):
        return self.c.post(URL, {'source_warehouse': str(self.a.id),
                                 'destination_warehouse': str((dest or self.b).id),
                                 'items': [{'product': str(self.p.id), 'batch': str((batch or self.b1).id),
                                            'quantity': qty}]}, format='json')

    def test_wac(self):
        # (10*100 + 5*130) / 15 = 110.00
        self.assertEqual(product_wac(self.p), Decimal('110.00'))

    def test_transfer_moves_stock_posts_journal_at_wac_no_gst(self):
        r = self.post(4)
        self.assertEqual(r.status_code, 201, r.content)
        tr = StockTransfer.objects.get()
        self.assertEqual(tr.challan_number, f'STC/{fy_label(date.today())}/0001')
        self.assertLessEqual(len(tr.challan_number), 16)
        self.assertEqual(tr.total_value, Decimal('440.00'))
        self.assertEqual(StockPoint.objects.get(warehouse=self.a, batch=self.b1).quantity, 6)
        self.assertEqual(StockPoint.objects.get(warehouse=self.b, batch=self.b1).quantity, 4)
        gl = GeneralLedgerEntry.objects.filter(reference=tr.challan_number)
        self.assertEqual(gl.count(), 2)
        self.assertEqual({(e.account.code, e.debit, e.credit) for e in gl},
                         {('1300', Decimal('440.00'), Decimal('0.00')), ('1300', Decimal('0.00'), Decimal('440.00'))})
        self.assertFalse(GeneralLedgerEntry.objects.filter(account__name__icontains='GST').exists())
        self.assertEqual(r.json()['warnings'], [])

    def test_cannot_exceed_stock_at_source(self):
        r = self.post(11)
        self.assertEqual(r.status_code, 400)
        self.assertIn('only 10 in stock', str(r.content))
        self.assertFalse(StockTransfer.objects.exists())
        self.assertEqual(StockPoint.objects.get(warehouse=self.a, batch=self.b1).quantity, 10)

    def test_eway_warning_above_50k(self):
        StockPoint.objects.filter(warehouse=self.a, batch=self.b1).update(quantity=1000)
        r = self.post(500)  # WAC ≈ 100.64 × 500 > ₹50,000
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['warnings'], [EWAY_WARNING])
        self.assertIn('may require an e-way bill (thresholds vary by state); Cenvora does not generate e-way bills',
                      EWAY_WARNING)

    def test_different_gstin_rejected(self):
        other = Warehouse.objects.create(name='KA', gstin='29ABCDE1234F1Z5', created_by=self.t)
        r = self.post(1, dest=other)
        self.assertEqual(r.status_code, 400)
        self.assertIn(INTER_GSTIN_NOT_SUPPORTED[:40], r.content.decode())

    def test_never_in_gstr1(self):
        self.post(4)
        g = compute_gstr1(self.t, date.today().replace(day=1), date.today(), apply_carry=False)
        self.assertEqual((g['b2b'], g['b2cl'], g['b2cs'], g['cdnr'], g['cdnur']), ([], [], [], [], []))

    def test_preview_warns_before_saving_and_writes_nothing(self):
        StockPoint.objects.filter(warehouse=self.a, batch=self.b1).update(quantity=1000)
        r = self.c.post(URL + 'preview/', {'source_warehouse': str(self.a.id), 'destination_warehouse': str(self.b.id),
                                           'items': [{'product': str(self.p.id), 'batch': str(self.b1.id),
                                                      'quantity': 500}]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['warnings'], [EWAY_WARNING])
        self.assertFalse(StockTransfer.objects.exists())
        self.assertEqual(StockPoint.objects.get(warehouse=self.a, batch=self.b1).quantity, 1000)
        r = self.c.post(URL + 'preview/', {'source_warehouse': str(self.a.id), 'destination_warehouse': str(self.b.id),
                                           'items': [{'product': str(self.p.id), 'batch': str(self.b1.id),
                                                      'quantity': 2000}]}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_challan_serial_unique_per_fy_and_max_16(self):
        self.assertEqual(next_challan_number(self.t, date(2026, 11, 3)), 'STC/26-27/0001')
        self.post(1)
        self.post(1)
        nums = sorted(StockTransfer.objects.values_list('challan_number', flat=True))
        fy = fy_label(date.today())
        self.assertEqual(nums, [f'STC/{fy}/0001', f'STC/{fy}/0002'])
        # A new FY restarts the series.
        self.assertEqual(next_challan_number(self.t, date(2030, 4, 1)), 'STC/30-31/0001')
        self.assertEqual(fy_label(date(2027, 3, 31)), '26-27')

    def test_challan_print_has_rule_55_particulars(self):
        self.a.address = 'Plot 1, Cuttack'
        self.a.save()
        self.b.address = 'Unit 9, Bhubaneswar'
        self.b.save()
        self.t.business_address = 'HQ, Bhubaneswar'
        self.t.save()
        self.post(4)
        tr = StockTransfer.objects.get()
        html = self.c.get(f'{URL}{tr.id}/challan/').content.decode()
        for needle in ('DELIVERY CHALLAN', 'Rule 55', tr.challan_number, tr.transfer_date.strftime('%d-%m-%Y'),
                       'Consignor', 'Consignee', 'UAT', 'Plot 1, Cuttack', 'Unit 9, Bhubaneswar',
                       '21ABCDE1234F1Z5', '847130', 'Widget', '4 pcs', '440.00', 'Authorised Signatory'):
            self.assertIn(needle, html, needle)

"""GST Shield rules (Rudra CA): threshold matrix, aggregate turnover, registered view, periods."""
import datetime
from decimal import Decimal

from django.test import TestCase

from billing.gst_thresholds import (STATE_GST_RULES, composition_limit, gst_shield,
                                    registration_threshold, sales_summary)
from billing.models import Customer, PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Vendor
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase
from users.models import User

L = Decimal('100000')


class ThresholdMatrixTests(TestCase):
    def test_matrix(self):
        cases = [
            # state, business_type, special goods, expected lakh
            ('29', 'goods_only', False, 40), ('29', 'services', False, 20), ('29', 'mixed', False, 20),
            ('29', 'goods_only', True, 20), ('29', None, False, 20),
            ('36', 'goods_only', False, 20),   # Telangana: status quo
            ('34', 'goods_only', False, 20),   # Puducherry
            ('05', 'goods_only', False, 20),   # Uttarakhand
            ('18', 'goods_only', False, 40),   # Assam opted in
            ('01', 'goods_only', False, 40),   # J&K opted in
            ('14', 'goods_only', False, 10), ('14', 'services', False, 10), ('16', None, False, 10),
            ('15', 'goods_only', True, 10),
            (None, 'goods_only', False, 20),   # unknown state: conservative
        ]
        for st, bt, sp, lakh in cases:
            with self.subTest(state=st, type=bt, special=sp):
                self.assertEqual(registration_threshold(st, bt, sp)[0], Decimal(lakh) * L)

    def test_table_is_data(self):
        self.assertEqual(len(STATE_GST_RULES), 36)
        for r in STATE_GST_RULES.values():
            self.assertEqual(r['effective_from'], datetime.date(2019, 4, 1))

    def test_composition_limits(self):
        self.assertEqual(composition_limit('29', 'goods_only'), Decimal('150') * L)
        self.assertEqual(composition_limit('05', 'mixed'), Decimal('75') * L)
        self.assertEqual(composition_limit('34', 'goods_only'), Decimal('150') * L)  # Puducherry not in 75L list
        self.assertEqual(composition_limit('29', 'services'), Decimal('50') * L)


class TurnoverAndShieldTests(FourFixesBase):
    """Tenant: Karnataka, GSTIN 29XYZAB1234C1Z1 (registered)."""
    today = datetime.date(2026, 10, 22)

    def _inv(self, number, d, price, tax='18.00', customer=None, hsn='847130', status='final'):
        inv = SalesInvoice.objects.create(created_by=self.tenant, invoice_number=number, invoice_date=d,
                                          customer=customer or self.customer, total_amount=Decimal('0'), status=status)
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, quantity=1, price=Decimal(price),
                                        tax=Decimal(tax), amount=Decimal(price), hsn_sac_code=hsn)
        return inv

    def _cn(self, number, d, price):
        cn = CreditNote.objects.create(credit_note_number=number, date=d, customer=self.customer, reason='return',
                                       total_amount=Decimal(price) * Decimal('1.18'), created_by=self.tenant)
        CreditNoteItem.objects.create(credit_note=cn, product=self.product, quantity=1, price=Decimal(price),
                                      tax=Decimal('18.00'), amount=Decimal(price) * Decimal('1.18'), hsn_sac_code='847130')

    def _unregister(self, business_type=None, state='Karnataka'):
        self.tenant.gstin = None
        self.tenant.state = state
        self.tenant.business_type = business_type
        self.tenant.save()

    def test_turnover_is_taxable_value_excluding_gst_and_includes_exempt(self):
        self._inv('I1', datetime.date(2026, 5, 1), '100000.00')           # 18%
        self._inv('I2', datetime.date(2026, 6, 1), '50000.00', tax='0')   # exempt/nil
        s = sales_summary(self.tenant, datetime.date(2026, 4, 1), self.today)
        self.assertEqual(s['turnover'], Decimal('150000.00'))
        self.assertEqual(s['output_tax'], Decimal('18000.00'))

    def test_drafts_and_prior_fy_excluded(self):
        self._inv('D1', datetime.date(2026, 5, 1), '100000.00', status='draft')
        self._inv('P1', datetime.date(2026, 3, 31), '100000.00')
        self.assertEqual(sales_summary(self.tenant, datetime.date(2026, 4, 1), self.today)['turnover'], Decimal('0'))

    def test_credit_notes_this_fy_subtracted_and_floored_at_zero(self):
        self._inv('I1', datetime.date(2026, 5, 1), '10000.00')
        self._cn('CN-1', datetime.date(2026, 6, 1), '4000.00')
        self.assertEqual(sales_summary(self.tenant, datetime.date(2026, 4, 1), self.today)['turnover'], Decimal('6000.00'))
        self._cn('CN-2', datetime.date(2026, 7, 1), '20000.00')  # CN for a prior-FY sale
        self.assertEqual(sales_summary(self.tenant, datetime.date(2026, 4, 1), self.today)['turnover'], Decimal('0.00'))
        self._cn('CN-OLD', datetime.date(2026, 3, 1), '99999.00')  # prior-FY CN: ignored
        self.assertEqual(sales_summary(self.tenant, datetime.date(2026, 4, 1), self.today)['gross_taxable'], Decimal('10000.00'))

    def test_unregistered_threshold_alerts_and_liability(self):
        self._unregister('goods_only')
        self._inv('I1', datetime.date(2026, 5, 1), '3300000.00')  # 82.5% of ₹40L
        out = gst_shield(self.tenant, self.today)
        self.assertFalse(out['registered'])
        self.assertEqual(out['registration_threshold'], '4000000.00')
        self.assertEqual(out['alert'], 'approaching')
        self.assertIn('register within 30 days', out['alert_text'])
        self.assertFalse(out['liable_to_register'])
        self.assertNotIn('output_tax', out)  # collected / ITC hidden when unregistered
        self._inv('I2', datetime.date(2026, 6, 1), '800000.00')
        out = gst_shield(self.tenant, self.today)
        self.assertTrue(out['liable_to_register'])
        self.assertEqual(out['alert'], 'exceeded')

    def test_unregistered_default_type_is_conservative_20L(self):
        self._unregister(None)
        self.assertEqual(gst_shield(self.tenant, self.today)['registration_threshold'], '2000000.00')

    def test_special_goods_force_20L(self):
        self._unregister('goods_only')
        self._inv('I1', datetime.date(2026, 5, 1), '1000.00', hsn='24022010')
        self.assertEqual(gst_shield(self.tenant, self.today)['registration_threshold'], '2000000.00')

    def test_interstate_goods_compulsory_registration(self):
        self._unregister('goods_only')
        mh = Customer.objects.create(name='MH', state='Maharashtra', created_by=self.tenant)
        self._inv('I1', datetime.date(2026, 5, 1), '1000.00', customer=mh)
        self.assertTrue(gst_shield(self.tenant, self.today)['compulsory_registration_interstate'])

    def test_registered_view_uses_prior_fy(self):
        self._inv('P1', datetime.date(2025, 6, 1), '60000000.00')  # FY25-26 ₹6 Cr
        out = gst_shield(self.tenant, self.today)
        self.assertTrue(out['registered'])
        self.assertNotIn('registration_threshold', out)
        self.assertEqual(out['prior_fy_turnover'], '60000000.00')
        self.assertEqual(out['prior_fy_label'], 'FY 2025-26')
        self.assertFalse(out['composition_eligible'])
        self.assertFalse(out['qrmp_eligible'])
        self.assertEqual(out['hsn_digits'], 6)
        self.assertIn('Cenvora does not support e-invoicing', out['e_invoice_warning'])

    def test_registered_small_prior_fy(self):
        self._inv('P1', datetime.date(2025, 6, 1), '1000000.00')
        out = gst_shield(self.tenant, self.today)
        self.assertTrue(out['composition_eligible'])
        self.assertEqual(out['hsn_digits'], 4)
        self.assertIsNone(out['e_invoice_warning'])

    def test_period_month_vs_fy_collected_itc_net(self):
        self._inv('I-MAY', datetime.date(2026, 5, 10), '10000.00')
        self._inv('I-OCT', datetime.date(2026, 10, 5), '20000.00')
        self._cn('CN-OCT', datetime.date(2026, 10, 6), '5000.00')
        v = Vendor.objects.create(name='V', gstin='29AAAAA0000A1Z5', state='Karnataka', created_by=self.tenant)
        for n, d, st in (('B-MAY', datetime.date(2026, 5, 2), 'matched'), ('B-OCT', datetime.date(2026, 10, 2), 'pending')):
            b = PurchaseBill.objects.create(created_by=self.tenant, bill_number=n, bill_date=d, vendor=v,
                                            vendor_name=v.name, vendor_gstin=v.gstin, total_amount=Decimal('1180'),
                                            gstr2b_status=st)
            PurchaseBillItem.objects.create(purchase_bill=b, product=self.product, quantity=1, price=Decimal('1000'),
                                            tax=Decimal('18'), amount=Decimal('1180'), hsn_sac_code='847130')
        m = gst_shield(self.tenant, self.today, 'month')
        self.assertEqual((m['period_start'], m['period_end']), ('2026-10-01', '2026-10-31'))
        self.assertEqual(m['output_tax'], '2700.00')        # 3,600 − 900 CN
        self.assertEqual(m['itc'], '180.00')
        self.assertEqual(m['net_payable'], '2520.00')
        self.assertEqual((m['safe_itc'], m['at_risk_itc']), ('0.00', '180.00'))
        f = gst_shield(self.tenant, self.today, 'fy')
        self.assertEqual(f['output_tax'], '4500.00')
        self.assertEqual(f['itc'], '360.00')
        self.assertEqual((f['safe_itc'], f['at_risk_itc']), ('180.00', '180.00'))
        self.assertFalse(f['paid_available'])

    def test_endpoint(self):
        res = self.client.get('/api/analytics/gst-shield/?period=fy')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['period'], 'fy')


class NetSignTests(TurnoverAndShieldTests.__mro__[1]):
    """Net = Collected − ITC, sign preserved (payable > 0, credit < 0, zero)."""
    today = datetime.date(2026, 10, 22)
    _inv = TurnoverAndShieldTests._inv
    _cn = TurnoverAndShieldTests._cn

    def _bill(self, n, price):
        v, _ = Vendor.objects.get_or_create(name='V', gstin='29AAAAA0000A1Z5', state='Karnataka', created_by=self.tenant)
        b = PurchaseBill.objects.create(created_by=self.tenant, bill_number=n, bill_date=datetime.date(2026, 10, 2),
                                        vendor=v, vendor_name=v.name, vendor_gstin=v.gstin,
                                        total_amount=Decimal(price) * Decimal('1.18'), gstr2b_status='matched')
        PurchaseBillItem.objects.create(purchase_bill=b, product=self.product, quantity=1, price=Decimal(price),
                                        tax=Decimal('18'), amount=Decimal(price) * Decimal('1.18'), hsn_sac_code='847130')

    def _net(self):
        m = gst_shield(self.tenant, self.today, 'month')
        self.assertEqual(Decimal(m['net_payable']), Decimal(m['output_tax']) - Decimal(m['itc']))
        return m

    def test_payable(self):
        self._inv('I1', datetime.date(2026, 10, 5), '10000.00'); self._bill('B1', '4000')
        m = self._net()
        self.assertEqual((m['output_tax'], m['itc'], m['net_payable']), ('1800.00', '720.00', '1080.00'))

    def test_credit(self):
        self._inv('I1', datetime.date(2026, 10, 5), '10000.00'); self._bill('B1', '20000')
        m = self._net()
        self.assertEqual((m['output_tax'], m['itc'], m['net_payable']), ('1800.00', '3600.00', '-1800.00'))

    def test_zero(self):
        self._inv('I1', datetime.date(2026, 10, 5), '10000.00'); self._bill('B1', '10000')
        self.assertEqual(self._net()['net_payable'], '0.00')

    def test_credit_notes_exceeding_sales_give_negative_collected(self):
        # CN in October against a September invoice: collected for the month goes negative,
        # net = −CN tax − ITC (this is the live "Net credit ₹21.4K" shape).
        self._inv('I-SEP', datetime.date(2026, 9, 5), '50000.00')
        self._cn('CN-OCT', datetime.date(2026, 10, 6), '10000.00'); self._bill('B1', '10000')
        m = self._net()
        self.assertEqual((m['output_tax'], m['output_tax_gross'], m['credit_note_tax']), ('-1800.00', '0.00', '1800.00'))
        self.assertEqual(m['net_payable'], '-3600.00')

    def test_prior_fy_breakdown(self):
        self._inv('I-P', datetime.date(2025, 6, 1), '30000.00')
        self._cn('CN-P', datetime.date(2025, 7, 1), '40000.00')
        m = gst_shield(self.tenant, self.today, 'month')
        self.assertEqual((m['prior_fy_start'], m['prior_fy_end']), ('2025-04-01', '2026-03-31'))
        self.assertEqual((m['prior_fy_gross_taxable'], m['prior_fy_credit_note_taxable'], m['prior_fy_turnover']),
                         ('30000.00', '40000.00', '0.00'))

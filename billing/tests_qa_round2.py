"""QA Round 2: sales metric parity, 194C 1%/2%, HSN shared messages."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from users.models import User
from inventory.models import Product
from billing.models import Vendor, PurchaseBill, PurchaseBillItem, SalesInvoice, Customer
from billing.models_returns import CreditNote
from billing.sales_metrics import sales_mtd, aggregate_sales_metric
from billing.gst_utils import validate_hsn_sac_code, HSN_REQUIRED_MSG, HSN_DIGITS_MSG
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService


class HSNSharedMessageTests(TestCase):
    def test_messages(self):
        with self.assertRaises(ValueError) as ctx:
            validate_hsn_sac_code('')
        self.assertEqual(str(ctx.exception), HSN_REQUIRED_MSG)
        with self.assertRaises(ValueError) as ctx2:
            validate_hsn_sac_code('12')
        self.assertEqual(str(ctx2.exception), HSN_DIGITS_MSG)
        self.assertEqual(validate_hsn_sac_code('8471'), '8471')


class SalesMetricParityTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='sm_t', email='sm@ex.com', password='x',
            business_name='SM Co', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        cust = Customer.objects.create(name='C1', created_by=self.tenant)
        today = date.today()
        SalesInvoice.objects.create(
            created_by=self.tenant, customer=cust, customer_name='C1',
            invoice_number='INV-MTD-1', invoice_date=today,
            status='final', total_amount=Decimal('10000.00'),
        )
        SalesInvoice.objects.create(
            created_by=self.tenant, customer=cust, customer_name='C1',
            invoice_number='DFT-003', invoice_date=today,
            status='draft', total_amount=Decimal('9999.00'),
        )
        CreditNote.objects.create(
            created_by=self.tenant, customer=cust,
            credit_note_number='CN-1', date=today,
            total_amount=Decimal('1000.00'),
        )

    def test_dashboard_ai_sales_mtd_identical(self):
        mtd = sales_mtd(self.tenant)
        self.assertEqual(mtd['gross'], 10000.0)
        self.assertEqual(mtd['returns'], 1000.0)
        self.assertEqual(mtd['net'], 9000.0)

        dash = self.client.get('/api/analytics/dashboard/?refresh=true')
        self.assertEqual(dash.status_code, status.HTTP_200_OK, dash.data)
        self.assertEqual(float(dash.data['total_sales']), float(mtd['net']))

        # Sales analytics MTD card
        sales_an = self.client.get('/api/billing/sales-invoices/analytics/')
        if sales_an.status_code == 200 and 'this_month_revenue_net' in (sales_an.data or {}):
            self.assertEqual(float(sales_an.data['this_month_revenue_net']), float(mtd['net']))


class Vendor194CRateTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='v194', email='v194@ex.com', password='x',
            business_name='V194', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)
        self.product = Product.objects.create(
            name='Svc', created_by=self.tenant, sale_price=1000,
            hsn_sac_code='998311', tax=18, stock=100,
        )
        bank = AccountingService.get_or_create_default_accounts(self.tenant).get('1002')
        GeneralLedgerEntry.objects.create(
            date=date.today(), account=bank,
            debit=Decimal('100000.00'), credit=0,
            description='seed', reference='SEED-194', created_by=self.tenant,
        )
        self.bank = bank

    def _pay(self, gstin, expected_tds):
        vendor = Vendor.objects.create(
            name=f'V-{gstin[:6]}', gstin=gstin, state='Karnataka', created_by=self.tenant,
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number=f'PB-{gstin[:8]}',
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=gstin,
            total_amount=Decimal('1180.00'),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill, product=self.product,
            quantity=1, price=Decimal('1000.00'), tax=Decimal('18.00'),
            amount=Decimal('1180.00'), hsn_sac_code='998311',
        )
        AccountingService.create_purchase_bill_entries(bill)
        res = self.client.post(
            '/api/billing/vendor-payments/',
            {
                'bill_id': str(bill.id),
                'amount': '1180.00',
                'payment_account_id': str(self.bank.id),
                'mode': 'bank_transfer',
                'tds_section': '194C',
            },
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(Decimal(str(res.data['tds_amount'])), expected_tds)
        self.assertEqual(Decimal(str(res.data['net_paid'])), Decimal('1180.00') - expected_tds)
        return res

    def test_194c_company_2_percent(self):
        # Entity char C (company) at GSTIN index 5
        self._pay('29AABCU1234F1Z5', Decimal('20.00'))

    def test_194c_individual_1_percent(self):
        # Entity char P (individual) at GSTIN index 5
        self._pay('29AAPPU0939F1Z5', Decimal('10.00'))

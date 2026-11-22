from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from billing.models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Customer, Vendor
from inventory.models import Product
from billing.sync_service import DocumentSyncService
from ledger.services import AccountingService
from ledger.models import GeneralLedgerEntry

User = get_user_model()

class AdditionalChargesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testtenant',
            email='test@example.com',
            password='password123'
        )
        self.customer = Customer.objects.create(
            name='Acme Corp',
            email='acme@example.com',
            state='Maharashtra',
            gstin='27ABCDE1234F1Z5',
            created_by=self.user
        )
        self.vendor = Vendor.objects.create(
            name='Vendor Supplies',
            state='Maharashtra',
            gstin='27XYZAB5678C1Z2',
            created_by=self.user
        )
        self.product = Product.objects.create(
            name='Widget A',
            price=Decimal('100.00'),
            created_by=self.user
        )

    def test_process_additional_charges_helper(self):
        charges = [
            {'name': 'Freight & Shipping', 'amount': '500.00', 'tax_rate': '18.00'},
            {'name': 'Packaging', 'amount': '100.00', 'tax_rate': '0.00'},
            {'name': 'Invalid / Zero', 'amount': '0.00', 'tax_rate': '5.00'},
        ]
        sanitized, taxable, tax, gross = DocumentSyncService.process_additional_charges(charges)
        self.assertEqual(len(sanitized), 2)
        self.assertEqual(taxable, Decimal('600.00'))
        self.assertEqual(tax, Decimal('90.00')) # 18% of 500
        self.assertEqual(gross, Decimal('690.00'))

    def test_sales_invoice_with_charges_and_ledger_balance(self):
        charges = [
            {'name': 'Freight & Shipping', 'amount': 200, 'tax_rate': 18},
            {'name': 'Loading & Unloading', 'amount': 50, 'tax_rate': 0},
        ]
        sanitized, _, _, charges_gross = DocumentSyncService.process_additional_charges(charges)
        
        # Item: 2 x 500 @ 18% GST -> 1000 taxable + 180 tax = 1180
        # Charges: 200 @ 18% GST + 50 @ 0% -> 250 taxable + 36 tax = 286
        # Total unrounded = 1180 + 286 = 1466.00
        invoice = SalesInvoice.objects.create(
            customer=self.customer,
            customer_name=self.customer.name,
            invoice_number='INV-TEST-001',
            invoice_date='2026-11-22',
            place_of_supply='Maharashtra',
            additional_charges=sanitized,
            total_amount=Decimal('1466.00'),
            round_off=Decimal('0.00'),
            status='final',
            created_by=self.user
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=invoice,
            product=self.product,
            quantity=Decimal('2'),
            price=Decimal('500.00'),
            tax=Decimal('18.00'),
            amount=Decimal('1000.00')
        )

        # Recalculate document total
        total = DocumentSyncService.recalculate_document_total(invoice)
        self.assertEqual(total, Decimal('1466.00'))

        # Post general ledger entries
        AccountingService.create_sales_invoice_entries(invoice)

        entries = GeneralLedgerEntry.objects.filter(sales_invoice=invoice)
        tot_dr = sum(e.debit for e in entries)
        tot_cr = sum(e.credit for e in entries)

        # AR should be debited with 1466
        self.assertEqual(tot_dr, Decimal('1466.00'))
        # Total credits should equal total debits
        self.assertEqual(tot_cr, Decimal('1466.00'))
        self.assertEqual(tot_dr - tot_cr, Decimal('0.00'))

    def test_purchase_bill_with_charges_and_ledger_balance(self):
        charges = [
            {'name': 'Freight Inward', 'amount': 300, 'tax_rate': 5},
        ]
        sanitized, _, _, charges_gross = DocumentSyncService.process_additional_charges(charges)

        # Item: 1 x 1000 @ 18% GST -> 1000 taxable + 180 tax = 1180
        # Charges: 300 @ 5% GST -> 300 taxable + 15 tax = 315
        # Total = 1495.00
        bill = PurchaseBill.objects.create(
            vendor=self.vendor,
            vendor_name=self.vendor.name,
            bill_number='BILL-TEST-001',
            bill_date='2026-11-22',
            place_of_supply='Maharashtra',
            additional_charges=sanitized,
            total_amount=Decimal('1495.00'),
            round_off=Decimal('0.00'),
            created_by=self.user
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=Decimal('1'),
            price=Decimal('1000.00'),
            tax=Decimal('18.00'),
            amount=Decimal('1000.00')
        )

        AccountingService.create_purchase_bill_entries(bill)

        entries = GeneralLedgerEntry.objects.filter(purchase_bill=bill)
        tot_dr = sum(e.debit for e in entries)
        tot_cr = sum(e.credit for e in entries)

        # AP should be credited with 1495
        self.assertEqual(tot_cr, Decimal('1495.00'))
        # Total debits should equal total credits
        self.assertEqual(tot_dr, Decimal('1495.00'))
        self.assertEqual(tot_dr - tot_cr, Decimal('0.00'))

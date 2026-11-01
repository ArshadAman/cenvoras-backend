"""
Comprehensive tests for Pre-Launch Accounting & Backend Fixes:
1. Payroll accrual double-entry balancing with Account 4200 (Rounding Off).
2. October 2026 payroll roundoff correction management command.
3. Universal repair_round_off_entries endpoint handling payroll and general ledger imbalances.
4. Balance sheet Opening Reserves vs Current Year Net Profit split.
5. Standardized customer validation error message ('Please select a customer').
6. SalesInvoiceItem remaining_qty serialization.
7. Sales invoice list search filter across multiple fields.
"""
import datetime
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from users.models import User
from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from billing.models_returns import CreditNote, CreditNoteItem
from billing.serializers import SalesInvoiceSerializer, SalesInvoiceItemSerializer
from inventory.models import Product
from hr.models import Department, Designation, Employee, PayrollRun, Payslip
from hr.services.hr_accounting_service import HRAccountingService
from ledger.models import Account, AccountType, GeneralLedgerEntry
from ledger.services import AccountingService


class PreLaunchBackendFixesTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username='prelaunch_tenant',
            email='prelaunch@ex.com',
            password='testpassword',
            business_name='Prelaunch Enterprise',
            role='admin',
            state='Karnataka',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.tenant)

        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Engineer')
        self.emp = Employee.objects.create(
            tenant=self.tenant,
            full_name='Test Employee',
            date_of_birth=datetime.date(1992, 4, 10),
            date_of_joining=datetime.date(2024, 1, 1),
            gender='M',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Karnataka',
            status='active',
            employee_code='PRE001',
        )

    def test_payroll_accrual_rounding_credit_higher_than_debit(self):
        """
        October bug scenario: Net pay is rounded up by ₹0.09.
        Credits > Debits, so Dr Rounding Off 4200 is posted.
        """
        payroll_run = PayrollRun.objects.create(
            tenant=self.tenant, month=10, year=2026, status='approved',
        )
        # Gross = 1000.00, Net = 900.09 (rounded up), PF = 100.00
        # Dr: 1000.00
        # Cr: 900.09 (2250) + 100.00 (2251) = 1000.09
        # round_diff = 1000.00 - 1000.09 = -0.09 -> Dr. Rounding Off 0.09
        Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=payroll_run,
            employee=self.emp,
            present_days=Decimal('26'),
            total_working_days=26,
            lop_days=Decimal('0'),
            gross_salary=Decimal('1000.00'),
            net_salary=Decimal('900.09'),
            employee_pf=Decimal('100.00'),
            employer_pf=Decimal('0.00'),
            employer_total_contribution=Decimal('0.00'),
            deductions={},
        )
        HRAccountingService.post_payroll_accrual(payroll_run, self.tenant)

        ref = f"PAYROLL-ACCRUAL-{payroll_run.year}-{payroll_run.month:02d}"
        entries = GeneralLedgerEntry.objects.filter(created_by=self.tenant, reference=ref)
        tot_dr = sum((e.debit for e in entries), Decimal('0.00'))
        tot_cr = sum((e.credit for e in entries), Decimal('0.00'))
        self.assertEqual(tot_dr, tot_cr, "Accrual entries must balance Dr == Cr")

        roundoff_entry = entries.filter(account__code='4200').first()
        self.assertIsNotNone(roundoff_entry)
        self.assertEqual(roundoff_entry.debit, Decimal('0.09'))
        self.assertEqual(roundoff_entry.credit, Decimal('0.00'))

    def test_payroll_accrual_rounding_debit_higher_than_credit(self):
        """
        Net pay rounded down: Dr > Cr, so Cr Rounding Off 4200 is posted.
        """
        payroll_run = PayrollRun.objects.create(
            tenant=self.tenant, month=11, year=2026, status='approved',
        )
        # Gross = 1000.09, Net = 900.00, PF = 100.00
        # Dr: 1000.09
        # Cr: 900.00 + 100.00 = 1000.00
        # round_diff = 1000.09 - 1000.00 = +0.09 -> Cr. Rounding Off 0.09
        Payslip.objects.create(
            tenant=self.tenant,
            payroll_run=payroll_run,
            employee=self.emp,
            present_days=Decimal('26'),
            total_working_days=26,
            lop_days=Decimal('0'),
            gross_salary=Decimal('1000.09'),
            net_salary=Decimal('900.00'),
            employee_pf=Decimal('100.00'),
            employer_pf=Decimal('0.00'),
            employer_total_contribution=Decimal('0.00'),
            deductions={},
        )
        HRAccountingService.post_payroll_accrual(payroll_run, self.tenant)

        ref = f"PAYROLL-ACCRUAL-{payroll_run.year}-{payroll_run.month:02d}"
        entries = GeneralLedgerEntry.objects.filter(created_by=self.tenant, reference=ref)
        tot_dr = sum((e.debit for e in entries), Decimal('0.00'))
        tot_cr = sum((e.credit for e in entries), Decimal('0.00'))
        self.assertEqual(tot_dr, tot_cr, "Accrual entries must balance Dr == Cr")

        roundoff_entry = entries.filter(account__code='4200').first()
        self.assertIsNotNone(roundoff_entry)
        self.assertEqual(roundoff_entry.credit, Decimal('0.09'))
        self.assertEqual(roundoff_entry.debit, Decimal('0.00'))

    def test_correct_october_payroll_roundoff_command(self):
        """
        Test management command correct_october_payroll_roundoff.
        """
        ref = "PAYROLL-ACCRUAL-2026-10"
        accounts = HRAccountingService.get_or_create_hr_accounts(self.tenant)

        # Simulate unbalanced October entries: Dr 1000.00, Cr 1000.09
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2026, 10, 31),
            account=accounts['5300'],
            debit=Decimal('1000.00'),
            credit=Decimal('0.00'),
            reference=ref,
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2026, 10, 31),
            account=accounts['2250'],
            debit=Decimal('0.00'),
            credit=Decimal('1000.09'),
            reference=ref,
            created_by=self.tenant,
        )

        # Run command
        call_command('correct_october_payroll_roundoff')

        entries = GeneralLedgerEntry.objects.filter(reference=ref, created_by=self.tenant)
        tot_dr = sum((e.debit for e in entries), Decimal('0.00'))
        tot_cr = sum((e.credit for e in entries), Decimal('0.00'))
        self.assertEqual(tot_dr, tot_cr)
        self.assertEqual(tot_dr, Decimal('1000.09'))

    def test_repair_round_off_entries_engine(self):
        """
        Test universal repair_round_off_entries fixes unbalanced payroll runs and arbitrary GL references.
        """
        # Create an unbalanced arbitrary GL reference
        accounts = AccountingService.get_or_create_default_accounts(self.tenant)
        ref = "TEST-ADJ-REF"
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2026, 10, 15),
            account=accounts['4001'],
            debit=Decimal('0.00'),
            credit=Decimal('500.50'),
            reference=ref,
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2026, 10, 15),
            account=accounts['1002'],
            debit=Decimal('500.00'),
            credit=Decimal('0.00'),
            reference=ref,
            created_by=self.tenant,
        )

        res = self.client.post('/api/ledger/repair-round-off/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['success'])
        self.assertIn(ref, res.data['repaired_gl_entries'])

        entries = GeneralLedgerEntry.objects.filter(reference=ref, created_by=self.tenant)
        tot_dr = sum((e.debit for e in entries), Decimal('0.00'))
        tot_cr = sum((e.credit for e in entries), Decimal('0.00'))
        self.assertEqual(tot_dr, tot_cr)

    def test_balance_sheet_opening_reserves_split(self):
        """
        Test that Balance Sheet splits Retained Earnings into Opening Reserves and Current Year Net Profit.
        """
        accounts = AccountingService.get_or_create_default_accounts(self.tenant)

        # Revenue before current FY (e.g. 2025-11-15 in FY 2025-2026)
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2025, 11, 15),
            account=accounts['4001'],
            debit=Decimal('0.00'),
            credit=Decimal('10000.00'),
            reference="PRE-FY-SALES",
            created_by=self.tenant,
        )
        # Revenue during current FY (e.g. 2026-06-20 in FY 2026-2027)
        GeneralLedgerEntry.objects.create(
            date=datetime.date(2026, 6, 20),
            account=accounts['4001'],
            debit=Decimal('0.00'),
            credit=Decimal('5000.00'),
            reference="CURR-FY-SALES",
            created_by=self.tenant,
        )

        res = self.client.get('/api/ledger/balance-sheet/?as_of=2026-10-31')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        equity = res.data['equity']

        self.assertEqual(equity['opening_reserves'], 10000.0)
        self.assertEqual(equity['current_year_net_profit'], 5000.0)
        self.assertEqual(equity['retained_earnings'], 15000.0)

    def test_customer_validation_error_message(self):
        """
        Standardized customer validation message: 'Please select a customer'.
        """
        factory = APIRequestFactory()
        req = factory.post('/api/billing/sales-invoices/')
        req.user = self.tenant

        data = {
            'customer_name': '',
            'invoice_number': 'INV-TEST-001',
            'status': 'final',
            'items': [{'product': 'Test', 'quantity': 1, 'price': '10.00', 'amount': '10.00'}]
        }
        serializer = SalesInvoiceSerializer(data=data, context={'request': req})
        self.assertFalse(serializer.is_valid())
        self.assertIn('customer_name', serializer.errors)
        self.assertEqual(str(serializer.errors['customer_name'][0]), 'Please select a customer')

    def test_sales_invoice_item_remaining_qty_serialization(self):
        """
        SalesInvoiceItemSerializer includes remaining_qty after credit note deductions.
        """
        cust = Customer.objects.create(name='Acme Corp', created_by=self.tenant)
        prod = Product.objects.create(
            name='Gadget', unit='pcs', price=100.00, hsn_sac_code='84713010', created_by=self.tenant,
        )
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=cust,
            customer_name='Acme Corp',
            invoice_number='INV-RET-001',
            invoice_date=datetime.date(2026, 8, 1),
            total_amount=Decimal('1000.00'),
            status='final',
        )
        item = SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=prod,
            quantity=10,
            price=Decimal('100.00'),
            amount=Decimal('1000.00'),
        )

        # Before credit note: remaining_qty == 10
        ser = SalesInvoiceItemSerializer(item)
        self.assertEqual(ser.data['remaining_qty'], 10.0)

        # Issue Credit Note for 3 units
        cn = CreditNote.objects.create(
            created_by=self.tenant,
            customer=cust,
            original_invoice=inv,
            credit_note_number='CN-001',
            date=datetime.date(2026, 8, 15),
            total_amount=Decimal('300.00'),
        )
        CreditNoteItem.objects.create(
            credit_note=cn,
            product=prod,
            quantity=3,
            price=Decimal('100.00'),
            amount=Decimal('300.00'),
        )

        # After credit note: remaining_qty == 7
        ser2 = SalesInvoiceItemSerializer(item)
        self.assertEqual(ser2.data['remaining_qty'], 7.0)

    def test_sales_invoice_search_filter(self):
        """
        Sales invoice search parameter filters across invoice number, customer name, PO number, challan, product.
        """
        cust1 = Customer.objects.create(name='Alpha Enterprises', created_by=self.tenant)
        cust2 = Customer.objects.create(name='Beta Solutions', created_by=self.tenant)
        prod = Product.objects.create(
            name='Special Widget R24', unit='pcs', price=50.00, hsn_sac_code='84713010', created_by=self.tenant,
        )

        inv1 = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=cust1,
            customer_name='Alpha Enterprises',
            invoice_number='INV-2026-001',
            po_number='PO-999',
            challan_number='CH-100',
            invoice_date=datetime.date(2026, 9, 1),
            total_amount=Decimal('500.00'),
            status='final',
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv1, product=prod, quantity=10, price=Decimal('50.00'), amount=Decimal('500.00'),
        )

        inv2 = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=cust2,
            customer_name='Beta Solutions',
            invoice_number='INV-2026-002',
            po_number='PO-888',
            invoice_date=datetime.date(2026, 9, 2),
            total_amount=Decimal('300.00'),
            status='final',
        )

        # Search by product name 'R24'
        res = self.client.get('/api/billing/sales-invoices/?search=R24')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        numbers = [inv['invoice_number'] for inv in res.data]
        self.assertIn('INV-2026-001', numbers)
        self.assertNotIn('INV-2026-002', numbers)

        # Search by PO 'PO-888'
        res2 = self.client.get('/api/billing/sales-invoices/?search=PO-888')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        numbers2 = [inv['invoice_number'] for inv in res2.data]
        self.assertIn('INV-2026-002', numbers2)
        self.assertNotIn('INV-2026-001', numbers2)

        # Search by challan 'CH-100'
        res3 = self.client.get('/api/billing/sales-invoices/?search=CH-100')
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        numbers3 = [inv['invoice_number'] for inv in res3.data]
        self.assertIn('INV-2026-001', numbers3)
        self.assertNotIn('INV-2026-002', numbers3)

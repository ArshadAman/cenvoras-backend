from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from ledger.models import Account, AccountType, GeneralLedgerEntry
from ledger.services import AccountingService
from billing.models import SalesInvoice, Customer, Vendor
from datetime import date

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class LedgerDeletionPolicyTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.user = User.objects.create_user(
			username="ledger_user",
			email="ledger@test.com",
			password="testpassword",
			first_name="Ledger",
			last_name="User",
		)
		self.client.force_authenticate(user=self.user)

		self.asset_account = Account.objects.create(
			code="1001",
			name="Cash",
			account_type=AccountType.ASSET,
			created_by=self.user,
		)
		self.revenue_account = Account.objects.create(
			code="4001",
			name="Sales",
			account_type=AccountType.REVENUE,
			created_by=self.user,
		)

	def test_debit_entry_cannot_be_deleted(self):
		entry = GeneralLedgerEntry.objects.create(
			date=date.today(),
			account=self.asset_account,
			debit=100,
			credit=0,
			description="Debit entry",
			created_by=self.user,
		)

		res = self.client.delete(f"/api/ledger/general-ledger-entry/{entry.id}/")
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("cannot be deleted individually", str(res.data))

	def test_credit_entry_linked_to_invoice_cannot_be_deleted(self):
		invoice = SalesInvoice.objects.create(
			created_by=self.user,
			customer_name="Jane",
			invoice_number="INV-LGR-PAID",
			invoice_date=date.today(),
			total_amount=100,
			amount_paid=100,
			payment_status="paid",
		)
		entry = GeneralLedgerEntry.objects.create(
			date=date.today(),
			account=self.revenue_account,
			debit=0,
			credit=100,
			description="Credit linked paid",
			sales_invoice=invoice,
			created_by=self.user,
		)

		res = self.client.delete(f"/api/ledger/general-ledger-entry/{entry.id}/")
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("linked to a Sales Invoice", str(res.data))

	def test_general_credit_entry_cannot_be_deleted(self):
		entry = GeneralLedgerEntry.objects.create(
			date=date.today(),
			account=self.revenue_account,
			debit=0,
			credit=50,
			description="General credit",
			created_by=self.user,
		)

		res = self.client.delete(f"/api/ledger/general-ledger-entry/{entry.id}/")
		self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("cannot be deleted individually", str(res.data))


class PaymentLedgerInvoiceReferenceTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="payment_ledger_user",
			email="payment_ledger@test.com",
			password="testpassword",
			first_name="Payment",
			last_name="Tester",
		)
		from billing.models import Customer, SalesInvoice
		self.customer = Customer.objects.create(
			name="Acme Corp",
			created_by=self.user,
		)
		self.invoice = SalesInvoice.objects.create(
			created_by=self.user,
			customer=self.customer,
			customer_name=self.customer.name,
			invoice_number="INV-TEST-001",
			invoice_date=date.today(),
			total_amount=500,
			amount_paid=0,
			payment_status="pending",
		)

	def test_payment_received_entries_link_to_sales_invoice(self):
		"""Both debit and credit ledger entries created for a payment must reference the sales_invoice"""
		from billing.models import Payment

		payment = Payment.objects.create(
			customer=self.customer,
			invoice=self.invoice,
			amount=200,
			mode="bank_transfer",
			date=date.today(),
			created_by=self.user,
		)

		entries = list(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}"))
		self.assertEqual(len(entries), 2)
		for entry in entries:
			self.assertIsNotNone(entry.sales_invoice, "sales_invoice on GeneralLedgerEntry must not be NULL")
			self.assertEqual(entry.sales_invoice.id, self.invoice.id)
			self.assertEqual(entry.customer.id, self.customer.id)

	def test_unlinked_payment_received_has_null_invoice_and_advances_account(self):
		"""Payments without an invoice reference customer advances with null sales_invoice"""
		from billing.models import Payment
		payment = Payment.objects.create(
			customer=self.customer,
			amount=150,
			mode="cash",
			date=date.today(),
			created_by=self.user,
		)

		entries = list(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}"))
		self.assertEqual(len(entries), 2)
		for entry in entries:
			self.assertIsNone(entry.sales_invoice)
		
		# Check credit entry account is Customer Advances (code 2101)
		credit_entry = next(e for e in entries if e.credit > 0)
		self.assertEqual(credit_entry.account.code, "2101")

	def test_rebuild_payment_ledger_entries_command(self):
		"""Rebuilding payment ledger entries must properly link to sales_invoice"""
		from billing.models import Payment
		from django.core.management import call_command

		payment = Payment.objects.create(
			customer=self.customer,
			invoice=self.invoice,
			amount=300,
			mode="upi",
			date=date.today(),
			created_by=self.user,
		)

		# Wipe ledger entries to simulate legacy state
		GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}").delete()
		self.assertEqual(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}").count(), 0)

		# Run management command
		call_command("rebuild_payment_ledger_entries", tenant_id=str(self.user.id))

		entries = list(GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}"))
		self.assertEqual(len(entries), 2)
		for entry in entries:
			self.assertIsNotNone(entry.sales_invoice)
			self.assertEqual(entry.sales_invoice.id, self.invoice.id)

	def test_payment_made_links_to_purchase_bill(self):
		"""Payment made entries link to purchase_bill when provided"""
		from billing.models import PurchaseBill, Vendor
		from ledger.services import AccountingService

		vendor = Vendor.objects.create(name="Supplier XYZ", created_by=self.user)
		bill = PurchaseBill.objects.create(
			vendor=vendor,
			bill_number="BILL-001",
			bill_date=date.today(),
			total_amount=1000,
			created_by=self.user,
		)

		AccountingService.create_payment_made_entries(
			vendor_name=vendor.name,
			amount=500,
			description="Payment for Bill 001",
			date=date.today(),
			user=self.user,
			purchase_bill=bill,
			payment_mode="bank",
		)

		entries = list(GeneralLedgerEntry.objects.filter(purchase_bill=bill))
		self.assertEqual(len(entries), 2)
		for entry in entries:
			self.assertEqual(entry.purchase_bill.id, bill.id)
			self.assertEqual(entry.vendor.id, vendor.id)


@override_settings(SECURE_SSL_REDIRECT=False)
class PartnerStatementTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.user = User.objects.create_user(
			username="statement_tester",
			email="statement@test.com",
			password="testpassword",
			first_name="Statement",
			last_name="Tester",
		)
		self.client.force_authenticate(user=self.user)
		self.customer = Customer.objects.create(
			name="Acme Corp",
			created_by=self.user
		)
		self.accounts = AccountingService.get_or_create_default_accounts(self.user)

	def test_opening_and_running_balance_calculation(self):
		from datetime import date, timedelta
		d_past = date(2024, 1, 15)
		d_period1 = date(2024, 2, 5)
		d_period2 = date(2024, 2, 20)

		# 1. Past invoice (prior to period date_from) -> establishes opening balance of ₹1,000 Dr
		GeneralLedgerEntry.objects.create(
			date=d_past,
			account=self.accounts['accounts_receivable'],
			debit=1000,
			credit=0,
			description="Past invoice",
			customer=self.customer,
			created_by=self.user
		)

		# 2. Period Invoice 1 -> ₹500 Dr (running balance becomes ₹1,500 Dr)
		GeneralLedgerEntry.objects.create(
			date=d_period1,
			account=self.accounts['accounts_receivable'],
			debit=500,
			credit=0,
			description="Period Invoice 1",
			customer=self.customer,
			created_by=self.user
		)

		# 3. Period Payment -> ₹300 Cr (running balance becomes ₹1,200 Dr)
		GeneralLedgerEntry.objects.create(
			date=d_period2,
			account=self.accounts['accounts_receivable'],
			debit=0,
			credit=300,
			description="Period Payment",
			customer=self.customer,
			created_by=self.user
		)

		# Request statement for Feb 2024
		res = self.client.get(
			f"/api/ledger/partner-statement/?partner_type=customer&partner_id={self.customer.id}&date_from=2024-02-01&date_to=2024-02-28"
		)
		self.assertEqual(res.status_code, status.HTTP_200_OK)
		data = res.data

		# Check Opening Balance
		self.assertEqual(float(data['opening_balance']), 1000.0)
		self.assertEqual(data['opening_balance_type'], 'Dr')

		# Check Period totals
		self.assertEqual(float(data['period_debit_total']), 500.0)
		self.assertEqual(float(data['period_credit_total']), 300.0)

		# Check Closing Balance
		self.assertEqual(float(data['closing_balance']), 1200.0)
		self.assertEqual(data['closing_balance_type'], 'Dr')

		# Check Running Balances on entries (descending order by default)
		entries = data['entries']
		self.assertEqual(len(entries), 2)
		# Entry 0 is the newest (Feb 20 payment) -> running balance is 1200 Dr
		self.assertEqual(float(entries[0]['running_balance']), 1200.0)
		self.assertEqual(entries[0]['running_balance_type'], 'Dr')
		# Entry 1 is Feb 5 invoice -> running balance is 1500 Dr
		self.assertEqual(float(entries[1]['running_balance']), 1500.0)
		self.assertEqual(entries[1]['running_balance_type'], 'Dr')



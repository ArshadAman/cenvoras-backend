from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from ledger.models import Account, AccountType, GeneralLedgerEntry
from billing.models import SalesInvoice
from datetime import date

User = get_user_model()


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
		self.assertIn("Debit ledger entries cannot be deleted", str(res.data))

	def test_credit_entry_linked_to_paid_invoice_cannot_be_deleted(self):
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
		self.assertIn("non-pending sales bill", str(res.data))

	def test_credit_entry_linked_to_pending_invoice_can_be_deleted(self):
		invoice = SalesInvoice.objects.create(
			created_by=self.user,
			customer_name="Jane",
			invoice_number="INV-LGR-PENDING",
			invoice_date=date.today(),
			total_amount=100,
			amount_paid=0,
			payment_status="pending",
		)
		entry = GeneralLedgerEntry.objects.create(
			date=date.today(),
			account=self.revenue_account,
			debit=0,
			credit=100,
			description="Credit linked pending",
			sales_invoice=invoice,
			created_by=self.user,
		)

		res = self.client.delete(f"/api/ledger/general-ledger-entry/{entry.id}/")
		self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

	def test_general_credit_entry_can_be_deleted(self):
		entry = GeneralLedgerEntry.objects.create(
			date=date.today(),
			account=self.revenue_account,
			debit=0,
			credit=50,
			description="General credit",
			created_by=self.user,
		)

		res = self.client.delete(f"/api/ledger/general-ledger-entry/{entry.id}/")
		self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)


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


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

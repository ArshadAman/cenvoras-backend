"""
Unit tests for Task 2.5 (HistoricalDocumentArchive) & Task 2.6 (Account 3999 GL Balancing).
Tests:
- TC-GL-01: Migrating ₹45L Debtors, ₹28L Creditors, ₹20L Stock balances Trial Balance 100%
  with ₹37L net credit in Equity Account 3999 ('Opening Balance Suspense')
- HistoricalDocumentArchive creation via Tally XML commit API
- Read-only archive search & detail endpoints (/api/billing/archive/documents/) + tenant isolation
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models_archive import HistoricalDocumentArchive
from ledger.models import Account
from ledger.services import AccountingService
from users.migration_service import MigrationValidationGate
from users.tests_tally_importer import SAMPLE_TALLY_XML

User = get_user_model()


class ArchiveAndGLBalanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="archive_gl_tenant",
            email="archgl@cenvora.com",
            password="pass",
            business_name="Bharat Pharma Dist",
            state="Karnataka",
            gstin="29BHARA1234B1Z5",
        )
        self.other_tenant = User.objects.create_user(
            username="other_arch_tenant",
            email="otherarch@cenvora.com",
            password="pass",
            business_name="Other Dist",
            state="Maharashtra",
        )
        self.client.force_authenticate(user=self.tenant)

    def test_gl_balancing_with_account_3999_opening_balance_suspense(self):
        """
        TC-GL-01: Import ₹45L Dr Debtors, ₹28L Cr Creditors, ₹20L Stock.
        Verify Trial Balance is 100% balanced and Account 3999 holds net ₹37L Credit.
        """
        gate = MigrationValidationGate(tenant=self.tenant)
        res = gate.commit(
            products=[
                {"name": "Bulk Medicine A", "item_code": "BMA-1", "tax": "12", "price": "1000.00"},
            ],
            parties=[
                {"name": "Mega Chemist", "party_type": "customer", "opening_balance": "4500000.00"},
                {"name": "Cipla Direct", "party_type": "vendor", "opening_balance": "2800000.00"},
            ],
            batches=[
                {"product_name": "Bulk Medicine A", "item_code": "BMA-1", "batch_number": "LOT-1", "quantity": "2000", "cost_price": "1000.00"},
            ],
            cutover_date=date(2026, 4, 1),
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["created"]["gl_entries"], 6)

        tb = AccountingService.get_trial_balance(self.tenant)
        self.assertTrue(tb["is_balanced"])
        self.assertEqual(tb["total_debits"], tb["total_credits"])
        self.assertEqual(tb["total_debits"], Decimal("9300000.00"))

        suspense_acc = Account.objects.get(created_by=self.tenant, code="3999")
        self.assertEqual(suspense_acc.name, "Opening Balance Suspense")

        suspense_row = next(r for r in tb["accounts"] if r["account"].code == "3999")
        # Net Credit = Cr (45L + 20L) - Dr (28L) = 37L
        net_credit = suspense_row["credit_total"] - suspense_row["debit_total"]
        self.assertEqual(net_credit, Decimal("3700000.00"))

    def test_historical_document_archive_creation_and_search_api(self):
        """
        Uploading Tally XML via tally-commit persists <VOUCHER> into HistoricalDocumentArchive
        and exposes it via search & detail APIs with strict tenant isolation.
        """
        utf16_bytes = SAMPLE_TALLY_XML.encode("utf-16")
        upload = SimpleUploadedFile("DayBook.xml", utf16_bytes, content_type="application/xml")

        commit_resp = self.client.post("/api/users/migration/tally-commit/", {"file": upload}, format="multipart")
        self.assertEqual(commit_resp.status_code, 200)
        self.assertEqual(commit_resp.data["created"]["historical_archives"], 1)

        # Search endpoint
        list_resp = self.client.get("/api/billing/archive/documents/?q=TLY-INV-101")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.data["count"], 1)
        doc = list_resp.data["results"][0]
        self.assertEqual(doc["document_number"], "TLY-INV-101")
        self.assertEqual(doc["party_name"], "Sharma Hardware")
        self.assertEqual(len(doc["items"]), 1)

        # Detail endpoint
        detail_resp = self.client.get(f"/api/billing/archive/documents/{doc['id']}/")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.data["document_number"], "TLY-INV-101")

        # Tenant isolation check
        other_client = APIClient()
        other_client.force_authenticate(user=self.other_tenant)
        other_list = other_client.get("/api/billing/archive/documents/")
        self.assertEqual(other_list.status_code, 200)
        self.assertEqual(other_list.data["count"], 0)
        other_detail = other_client.get(f"/api/billing/archive/documents/{doc['id']}/")
        self.assertEqual(other_detail.status_code, 404)

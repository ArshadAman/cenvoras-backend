"""
Unit tests for Task 3.1: 1-Click Complete Business Backup ZIP Service (users/backup_service.py).
Tests:
- ZIP structure (manifest.json, json/full_backup.json, csv/*.csv, tally/Tally_Full_Backup_Inventory.xml)
- SHA-256 checksum verification for every file in manifest.json
- Strict tenant isolation (other tenant's data never leaks into backup)
- REST endpoint GET /api/users/backup/download-zip/
"""
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from inventory.models import Product
from users.backup_service import generate_complete_business_backup_zip

User = get_user_model()


class CompleteBackupZipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="backup_tenant",
            email="backup@cenvora.com",
            password="pass",
            business_name="Sovereign Distributors",
            state="Karnataka",
            gstin="29SOVER1234S1Z5",
        )
        self.other_tenant = User.objects.create_user(
            username="other_backup_tenant",
            email="otherbk@cenvora.com",
            password="pass",
            business_name="Secret Competitor",
            state="Maharashtra",
        )
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Loyal Medicals",
            gstin="29LOYAL1234L1Z2",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Dolo 650 Strip",
            hsn_sac_code="3004",
            unit="strip",
            price=Decimal("25.00"),
            sale_price=Decimal("32.00"),
            tax=Decimal("12.00"),
            created_by=self.tenant,
        )
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name="Loyal Medicals",
            invoice_number="INV-BK-001",
            invoice_date=date(2026, 11, 25),
            status="final",
            total_amount=Decimal("0.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            quantity=Decimal("5.000"),
            unit="strip",
            price=Decimal("32.00"),
            tax=Decimal("12.00"),
            amount=Decimal("179.20"),
        )

        # Other tenant's secret product (must never appear in self.tenant backup)
        Product.objects.create(
            name="SECRET_OTHER_ITEM",
            created_by=self.other_tenant,
        )

    def test_complete_backup_zip_integrity_and_isolation(self):
        """Verify ZIP entries, SHA-256 checksums, XML validity, and tenant isolation."""
        zip_bytes, filename = generate_complete_business_backup_zip(self.tenant)
        self.assertIn("SovereignDistributors_Complete_Backup_", filename)
        self.assertTrue(filename.endswith(".zip"))

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            expected = {
                "manifest.json",
                "json/full_backup.json",
                "csv/products.csv",
                "csv/batches_stock.csv",
                "csv/customers.csv",
                "csv/vendors.csv",
                "csv/sales_invoices.csv",
                "csv/purchase_bills.csv",
                "csv/general_ledger.csv",
                "tally/Tally_Full_Backup_Inventory.xml",
            }
            self.assertEqual(names, expected)

            # 1. Verify SHA-256 checksums in manifest.json
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["counts"]["products"], 1)
            self.assertEqual(manifest["counts"]["customers"], 1)
            self.assertEqual(manifest["counts"]["sales_invoices"], 1)
            for path, expected_hash in manifest["sha256_checksums"].items():
                actual_hash = hashlib.sha256(zf.read(path)).hexdigest()
                self.assertEqual(actual_hash, expected_hash, f"Checksum mismatch for {path}")

            # 2. Verify JSON contents & tenant isolation
            full_json_raw = zf.read("json/full_backup.json").decode("utf-8")
            self.assertNotIn("SECRET_OTHER_ITEM", full_json_raw)
            full_json = json.loads(full_json_raw)
            self.assertEqual(full_json["products"][0]["name"], "Dolo 650 Strip")
            self.assertEqual(full_json["sales_invoices"][0]["invoice_number"], "INV-BK-001")

            # 3. Verify Tally XML inside backup
            tally_xml = zf.read("tally/Tally_Full_Backup_Inventory.xml").decode("utf-8")
            ET.fromstring(tally_xml)
            self.assertIn("INV-BK-001", tally_xml)

    def test_backup_zip_api_endpoint(self):
        """Verify GET /api/users/backup/download-zip/ streams valid ZIP attachment."""
        resp = self.client.get("/api/users/backup/download-zip/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn("attachment; filename=", resp["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            self.assertIn("manifest.json", zf.namelist())

"""
Unit tests for 1-Click Monthly CA Audit Pack ZIP Generator (billing/ca_pack.py).
Tests:
- ZIP archive integrity and required file entries
- Tally_Vouchers_Accounting.xml validity via ElementTree and inclusion of saved CA profile
- Optional Tally_Vouchers_Full_Inventory.xml inclusion when requested
- GSTR_Audit_Registers.xlsx and Trial_Balance.xlsx readability via openpyxl
- GSTR_Portal_Payload.json validity via json.loads
- API endpoint GET /api/billing/tally/ca-audit-pack-zip/
"""
from datetime import date
from decimal import Decimal
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
import openpyxl
from rest_framework.test import APIClient

from billing.ca_pack import generate_ca_audit_pack_zip
from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from billing.models_ca import CAMappingProfile
from inventory.models import Product

User = get_user_model()


class CAAuditPackZipTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="zip_pack_tenant",
            email="zip@cenvora.com",
            password="pass",
            business_name="Shree Ganesh Traders",
            state="Karnataka",
            gstin="29SHREE1234G1Z2",
        )
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Metro Pharma",
            gstin="29METRO1234M1Z5",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Paracetamol 650mg",
            hsn_sac_code="3004",
            unit="strip",
            price=Decimal("20.00"),
            sale_price=Decimal("30.00"),
            tax=Decimal("12.00"),
            created_by=self.tenant,
        )

        # Custom CA profile to verify integration inside ZIP
        CAMappingProfile.objects.create(
            tenant=self.tenant,
            sales_account="CA Custom Zip Sales",
        )

        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name="Metro Pharma",
            invoice_number="INV-ZIP-001",
            invoice_date=date(2026, 11, 20),
            status="final",
            place_of_supply="Karnataka",
            total_amount=Decimal("0.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            quantity=Decimal("10.000"),
            unit="strip",
            price=Decimal("30.00"),
            tax=Decimal("12.00"),
            amount=Decimal("336.00"),
        )

    def test_generate_ca_audit_pack_zip_default_bundle(self):
        """Verify default ZIP bundle contains all 5 core files with valid content."""
        zip_bytes, filename = generate_ca_audit_pack_zip(
            tenant=self.tenant,
            start_date="2026-11-01",
            end_date="2026-11-30",
            include_inventory_xml=False,
        )
        self.assertTrue(filename.endswith(".zip"))
        self.assertIn("ShreeGaneshTraders_CA_Audit_Pack_20261101_to_20261130.zip", filename)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            expected = {
                "Tally_Vouchers_Accounting.xml",
                "GSTR_Audit_Registers.xlsx",
                "Trial_Balance.xlsx",
                "GSTR_Portal_Payload.json",
                "README_FOR_CA.txt",
            }
            self.assertEqual(names, expected)

            # 1. Validate Tally XML + CA profile integration
            xml_str = zf.read("Tally_Vouchers_Accounting.xml").decode("utf-8")
            root = ET.fromstring(xml_str)
            self.assertEqual(root.tag, "ENVELOPE")
            self.assertIn("INV-ZIP-001", xml_str)
            self.assertIn("CA Custom Zip Sales", xml_str)

            # 2. Validate GSTR Excel workbook
            gstr_wb = openpyxl.load_workbook(io.BytesIO(zf.read("GSTR_Audit_Registers.xlsx")))
            self.assertGreaterEqual(len(gstr_wb.sheetnames), 1)

            # 3. Validate Trial Balance Excel workbook
            tb_wb = openpyxl.load_workbook(io.BytesIO(zf.read("Trial_Balance.xlsx")))
            self.assertIn("Trial Balance", tb_wb.sheetnames)
            tb_ws = tb_wb["Trial Balance"]
            self.assertIn("Shree Ganesh Traders", str(tb_ws["A1"].value))

            # 4. Validate GSTR JSON
            json_data = json.loads(zf.read("GSTR_Portal_Payload.json").decode("utf-8"))
            self.assertIsInstance(json_data, dict)

            # 5. Validate README
            readme = zf.read("README_FOR_CA.txt").decode("utf-8")
            self.assertIn("Shree Ganesh Traders", readme)
            self.assertIn("Tally_Vouchers_Accounting.xml", readme)

    def test_generate_ca_audit_pack_zip_with_inventory_xml(self):
        """Verify include_inventory_xml=True bundles Tally_Vouchers_Full_Inventory.xml."""
        zip_bytes, _ = generate_ca_audit_pack_zip(
            tenant=self.tenant,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            include_inventory_xml=True,
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = set(zf.namelist())
            self.assertIn("Tally_Vouchers_Full_Inventory.xml", names)
            inv_xml = zf.read("Tally_Vouchers_Full_Inventory.xml").decode("utf-8")
            ET.fromstring(inv_xml)
            self.assertIn("<ALLINVENTORYENTRIES.LIST>", inv_xml)
            self.assertIn("<STOCKITEMNAME>Paracetamol 650mg</STOCKITEMNAME>", inv_xml)

    def test_ca_audit_pack_zip_api_endpoint(self):
        """Verify GET /api/billing/tally/ca-audit-pack-zip/ returns valid ZIP response."""
        resp = self.client.get(
            "/api/billing/tally/ca-audit-pack-zip/?start_date=2026-11-01&end_date=2026-11-30&include_inventory_xml=true"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn("attachment; filename=", resp["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as zf:
            self.assertIn("Tally_Vouchers_Accounting.xml", zf.namelist())
            self.assertIn("Tally_Vouchers_Full_Inventory.xml", zf.namelist())
            self.assertIn("Trial_Balance.xlsx", zf.namelist())

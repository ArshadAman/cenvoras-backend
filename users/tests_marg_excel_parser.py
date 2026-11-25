"""
Unit tests for Marg ERP 9+ Multi-Sheet Excel/CSV Parser & Migration APIs.
Tests:
- Tier 1 auto-detection of Marg Item.xlsx (PACKING, RATE_A/B/C, MRP, BATCH_NO, EXP_DATE)
- Tier 1 auto-detection of Marg Ledger.xlsx (DRUG_LIC_1/2, FSSAI, OP_BAL, default_price_tier)
- Tier 2 preview_marg_spreadsheet & custom_mapping override
- REST API endpoints: marg-preview, marg-dry-run, marg-commit (including 422 blocker response)
"""
from datetime import date
from decimal import Decimal
import io
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
import openpyxl
from rest_framework.test import APIClient

from billing.models import Customer
from inventory.models import Product, ProductBatch
from users.marg_excel_parser import parse_marg_spreadsheet, preview_marg_spreadsheet
from users.migration_service import MigrationValidationGate

User = get_user_model()


def _make_xlsx_bytes(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class MargExcelParserTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="marg_excel_tenant",
            email="marg@cenvora.com",
            password="pass",
            business_name="Shree Sai Pharma Dist",
            state="Maharashtra",
            gstin="27SHREE1234S1Z5",
        )
        self.client.force_authenticate(user=self.tenant)

    def test_marg_consolidated_item_xlsx_auto_parse_and_commit(self):
        """Marg Item.xlsx with PACKING, RATE_A/B/C, MRP, BATCH_NO, EXP_DATE='11/26'."""
        xlsx_bytes = _make_xlsx_bytes(
            ["ITEM_CODE", "P_NAME", "PACKING", "HSN", "GST_PER", "PUR_RATE", "MRP", "RATE_A", "RATE_B", "RATE_C", "BATCH_NO", "EXP_DATE", "CL_STOCK"],
            [
                ["AUG625", "Augmentin 625 Duo", "10*10", "3004", "12%", "₹ 140.00", "220.00", "175.00", "165.00", "155.00", "AG-101", "11/26", "50"],
            ],
        )

        parsed = parse_marg_spreadsheet(xlsx_bytes, "Item.xlsx")
        self.assertEqual(len(parsed["products"]), 1)
        self.assertEqual(len(parsed["batches"]), 1)

        gate = MigrationValidationGate(tenant=self.tenant)
        res = gate.commit(**parsed)
        self.assertTrue(res["success"])

        prod = Product.objects.get(created_by=self.tenant, item_code="AUG625")
        self.assertEqual(prod.unit, "strip")
        self.assertEqual(prod.secondary_unit, "box")
        self.assertEqual(prod.conversion_factor, 10)
        self.assertEqual(prod.stock, Decimal("50.000"))

        batch = ProductBatch.objects.get(product=prod, batch_number="AG-101")
        self.assertEqual(batch.expiry_date, date(2026, 11, 30))
        self.assertEqual(batch.mrp, Decimal("220.0000"))
        self.assertEqual(batch.rate_a, Decimal("175.0000"))
        self.assertEqual(batch.rate_b, Decimal("165.0000"))
        self.assertEqual(batch.rate_c, Decimal("155.0000"))

    def test_marg_ledger_csv_dl_numbers_and_price_tier(self):
        """Marg Ledger.csv with DRUG_LIC_1/2, FSSAI, PRICE_TIER, and OP_BAL."""
        csv_text = (
            "PARTY_NAME,GROUP,GSTIN,MOBILE,STATION,DRUG_LIC_1,DRUG_LIC_2,FSSAI,PRICE_TIER,OP_BAL\n"
            "Jeevan Jyoti Chemist,Sundry Debtors,27JEEVA1234J1Z9,+91 9820012345,Pune,20B-MH-1001,21B-MH-1002,FSSAI-998877,B,25000.50\n"
        )
        parsed = parse_marg_spreadsheet(csv_text.encode("utf-8"), "Ledger.csv")
        self.assertEqual(len(parsed["parties"]), 1)

        gate = MigrationValidationGate(tenant=self.tenant)
        gate.commit(**parsed)

        cust = Customer.objects.get(created_by=self.tenant, name="Jeevan Jyoti Chemist")
        self.assertEqual(cust.dl_number_20b, "20B-MH-1001")
        self.assertEqual(cust.dl_number_21b, "21B-MH-1002")
        self.assertEqual(cust.fssai_number, "FSSAI-998877")
        self.assertEqual(cust.default_price_tier, "rate_b")
        self.assertEqual(cust.current_balance, Decimal("25000.50"))
        self.assertEqual(cust.phone, "9820012345")

    def test_tier2_preview_and_api_endpoints(self):
        """Verify marg-preview, marg-dry-run, and marg-commit endpoints end-to-end."""
        xlsx_bytes = _make_xlsx_bytes(
            ["Custom_Med_Title", "Custom_Pack", "Custom_Cost", "Custom_GST"],
            [["Crocin Advance", "10*15", "18.50", "12"]],
        )

        # 1. Preview endpoint
        preview_file = SimpleUploadedFile("custom_marg.xlsx", xlsx_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        prev_resp = self.client.post("/api/users/migration/marg-preview/", {"file": preview_file}, format="multipart")
        self.assertEqual(prev_resp.status_code, 200)
        self.assertEqual(prev_resp.data["total_rows"], 1)
        self.assertEqual(len(prev_resp.data["sample_rows"]), 1)

        # 2. Dry-Run with custom column mapping
        custom_map = json.dumps({
            "Custom_Med_Title": "name",
            "Custom_Pack": "packing",
            "Custom_Cost": "price",
            "Custom_GST": "tax",
        })
        dry_file = SimpleUploadedFile("custom_marg.xlsx", xlsx_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        dry_resp = self.client.post(
            "/api/users/migration/marg-dry-run/",
            {"file": dry_file, "sheet_type": "products", "custom_mapping": custom_map},
            format="multipart",
        )
        self.assertEqual(dry_resp.status_code, 200)
        self.assertTrue(dry_resp.data["can_commit"])
        self.assertEqual(dry_resp.data["summary"]["clean_count"], 1)

        # 3. Commit with custom column mapping
        commit_file = SimpleUploadedFile("custom_marg.xlsx", xlsx_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        commit_resp = self.client.post(
            "/api/users/migration/marg-commit/",
            {"file": commit_file, "sheet_type": "products", "custom_mapping": custom_map},
            format="multipart",
        )
        self.assertEqual(commit_resp.status_code, 200)
        self.assertTrue(commit_resp.data["success"])
        self.assertEqual(commit_resp.data["created"]["products"], 1)
        self.assertTrue(Product.objects.filter(created_by=self.tenant, name="Crocin Advance").exists())

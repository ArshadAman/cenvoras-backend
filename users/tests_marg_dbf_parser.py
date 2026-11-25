"""
Unit tests for Marg ERP 9+ Tier 3 Raw FoxPro DBF Folder Parser (users/marg_dbf_parser.py).
Builds real binary dBase III (.DBF) files in memory, packages them into a ZIP,
and verifies extraction, item_code->product_name batch linking, validation,
and atomic commit via both parser and REST API endpoints.
"""
from datetime import date
from decimal import Decimal
import io
import struct
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Customer
from inventory.models import Product, ProductBatch
from users.marg_dbf_parser import parse_marg_dbf_zip
from users.migration_service import MigrationValidationGate

User = get_user_model()


def _build_dbf_bytes(fields: list[tuple[str, int]], records: list[dict]) -> bytes:
    """
    Construct a minimal valid dBase III (.DBF) binary file in memory.
    `fields`: list of (column_name_max_10, char_length)
    """
    num_records = len(records)
    header_len = 32 + (32 * len(fields)) + 1
    record_len = 1 + sum(length for _, length in fields)

    # 32-byte DBF file header (version=0x03, date=26/11/25)
    header = bytearray(32)
    header[0] = 0x03
    header[1], header[2], header[3] = 26, 11, 25
    struct.pack_into('<I', header, 4, num_records)
    struct.pack_into('<H', header, 8, header_len)
    struct.pack_into('<H', header, 10, record_len)

    # Field descriptors (32 bytes each)
    field_descs = bytearray()
    for col_name, length in fields:
        desc = bytearray(32)
        name_bytes = col_name.encode('ascii')[:10]
        desc[:len(name_bytes)] = name_bytes
        desc[11] = ord('C')  # Character field
        desc[16] = length
        field_descs.extend(desc)

    terminator = b'\x0d'

    # Records
    body = bytearray()
    for rec in records:
        body.append(0x20)  # Active (non-deleted) record flag
        for col_name, length in fields:
            val_str = str(rec.get(col_name, '')).encode('cp1252', errors='replace')
            padded = val_str[:length].ljust(length, b' ')
            body.extend(padded)

    eof = b'\x1a'
    return bytes(header + field_descs + terminator + body + eof)


class MargDBFParserTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="marg_dbf_tenant",
            email="dbf@cenvora.com",
            password="pass",
            business_name="FoxPro Pharma Agency",
            state="Delhi",
            gstin="07FOXPR1234F1Z2",
        )
        self.client.force_authenticate(user=self.tenant)

    def _create_marg_data_zip(self) -> bytes:
        item_dbf = _build_dbf_bytes(
            [("CODE", 10), ("ITNAME", 30), ("PACK", 10), ("HSNCODE", 8), ("TAX", 6), ("PRATE", 10), ("SRATE", 10)],
            [{"CODE": "PAN40", "ITNAME": "Pan 40 Tab", "PACK": "10*15", "HSNCODE": "3004", "TAX": "12", "PRATE": "95.00", "SRATE": "120.00"}],
        )
        batch_dbf = _build_dbf_bytes(
            [("CODE", 10), ("BATCH", 12), ("EXPDT", 8), ("CLQTY", 8), ("MRP", 8), ("RATEA", 8), ("RATEB", 8)],
            [{"CODE": "PAN40", "BATCH": "PN-882", "EXPDT": "08/27", "CLQTY": "200", "MRP": "155.00", "RATEA": "120.00", "RATEB": "112.00"}],
        )
        ledger_dbf = _build_dbf_bytes(
            [("ACNAME", 30), ("GRNAME", 18), ("GSTNO", 15), ("MOB", 12), ("DL1", 15), ("DL2", 15), ("OPBAL", 10)],
            [{"ACNAME": "Capital Chemist", "GRNAME": "Sundry Debtors", "GSTNO": "07CAPIT1234C1Z9", "MOB": "9811122233", "DL1": "20B-DL-11", "DL2": "21B-DL-22", "OPBAL": "18500.00"}],
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("DATA/ITEM.DBF", item_dbf)
            zf.writestr("DATA/BATCH.DBF", batch_dbf)
            zf.writestr("DATA/LEDGER.DBF", ledger_dbf)
        return buf.getvalue()

    def test_parse_marg_dbf_zip_and_commit(self):
        """Verify ITEM.DBF, BATCH.DBF (linked via CODE), and LEDGER.DBF extract and commit cleanly."""
        zip_bytes = self._create_marg_data_zip()
        parsed = parse_marg_dbf_zip(zip_bytes)

        self.assertEqual(len(parsed["products"]), 1)
        self.assertEqual(len(parsed["batches"]), 1)
        self.assertEqual(len(parsed["parties"]), 1)
        self.assertEqual(parsed["batches"][0]["product_name"], "Pan 40 Tab")

        gate = MigrationValidationGate(tenant=self.tenant)
        res = gate.commit(
            products=parsed["products"],
            parties=parsed["parties"],
            batches=parsed["batches"],
        )
        self.assertTrue(res["success"])

        prod = Product.objects.get(created_by=self.tenant, item_code="PAN40")
        self.assertEqual(prod.unit, "strip")
        self.assertEqual(prod.secondary_unit, "box")
        self.assertEqual(prod.conversion_factor, 10)
        self.assertEqual(prod.stock, Decimal("200.000"))

        batch = ProductBatch.objects.get(product=prod, batch_number="PN-882")
        self.assertEqual(batch.expiry_date, date(2027, 8, 31))
        self.assertEqual(batch.rate_a, Decimal("120.0000"))
        self.assertEqual(batch.rate_b, Decimal("112.0000"))

        cust = Customer.objects.get(created_by=self.tenant, name="Capital Chemist")
        self.assertEqual(cust.dl_number_20b, "20B-DL-11")
        self.assertEqual(cust.dl_number_21b, "21B-DL-22")
        self.assertEqual(cust.current_balance, Decimal("18500.00"))

    def test_marg_dbf_api_endpoints(self):
        """Verify POST /api/users/migration/marg-dbf-dry-run/ and marg-dbf-commit/."""
        zip_bytes = self._create_marg_data_zip()

        dry_file = SimpleUploadedFile("marg_data.zip", zip_bytes, content_type="application/zip")
        dry_resp = self.client.post("/api/users/migration/marg-dbf-dry-run/", {"file": dry_file}, format="multipart")
        self.assertEqual(dry_resp.status_code, 200)
        self.assertTrue(dry_resp.data["can_commit"])
        self.assertEqual(len(dry_resp.data["detected_files"]), 3)

        commit_file = SimpleUploadedFile("marg_data.zip", zip_bytes, content_type="application/zip")
        commit_resp = self.client.post("/api/users/migration/marg-dbf-commit/", {"file": commit_file}, format="multipart")
        self.assertEqual(commit_resp.status_code, 200)
        self.assertTrue(commit_resp.data["success"])
        self.assertTrue(Product.objects.filter(created_by=self.tenant, item_code="PAN40").exists())

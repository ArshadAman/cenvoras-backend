"""
Unit tests for TallyPrime XML Master & DayBook Ingestion Parser (users/tally_importer.py).
Tests:
- UTF-16LE BOM + illegal &#4; control entity sanitization
- <LEDGER> parsing for Sundry Debtors (negative opening balance -> positive receivable) and Sundry Creditors
- <STOCKITEM> parsing for Base/Additional units, conversion factor, HSN, GST%, and <BATCHALLOCATIONS.LIST>
- <VOUCHER> DayBook extraction (Sales voucher + inventory lines)
- REST API endpoints: POST /api/users/migration/tally-dry-run/ and tally-commit/
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Customer, Vendor
from inventory.models import Product, ProductBatch
from users.migration_service import MigrationValidationGate
from users.tally_importer import parse_tally_xml

User = get_user_model()

SAMPLE_TALLY_XML = """<?xml version="1.0" encoding="UTF-16"?>
<ENVELOPE>
  <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Sharma Hardware&#4;" ACTION="Create">
            <NAME>Sharma Hardware&#4;</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <PARTYGSTIN>29SHARM1234S1Z5</PARTYGSTIN>
            <LEDSTATENAME>Karnataka</LEDSTATENAME>
            <LEDGERMOBILE>9845011223</LEDGERMOBILE>
            <OPENINGBALANCE>-15400.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="Bosch India Ltd" ACTION="Create">
            <NAME>Bosch India Ltd</NAME>
            <PARENT>Sundry Creditors</PARENT>
            <PARTYGSTIN>29BOSCH1234B1Z2</PARTYGSTIN>
            <LEDSTATENAME>Karnataka</LEDSTATENAME>
            <OPENINGBALANCE>42000.00</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM NAME="Bosch Angle Grinder GWS 600" ACTION="Create">
            <NAME>Bosch Angle Grinder GWS 600</NAME>
            <PARENT>Power Tools</PARENT>
            <BASEUNITS>pcs</BASEUNITS>
            <ADDITIONALUNITS>box</ADDITIONALUNITS>
            <CONVERSION>4</CONVERSION>
            <PARTNO>GWS-600</PARTNO>
            <HSNCODE>8467</HSNCODE>
            <OPENINGBALANCE> 20 pcs</OPENINGBALANCE>
            <OPENINGRATE>2100.00/pcs</OPENINGRATE>
            <GSTDETAILS.LIST>
              <STATEWISEDETAILS.LIST>
                <RATEDETAILS.LIST>
                  <GSTRATEDUTYHEAD>Central Tax</GSTRATEDUTYHEAD>
                  <GSTRATE>9</GSTRATE>
                </RATEDETAILS.LIST>
                <RATEDETAILS.LIST>
                  <GSTRATEDUTYHEAD>State Tax</GSTRATEDUTYHEAD>
                  <GSTRATE>9</GSTRATE>
                </RATEDETAILS.LIST>
              </STATEWISEDETAILS.LIST>
            </GSTDETAILS.LIST>
            <BATCHALLOCATIONS.LIST>
              <GODOWNNAME>Peenya Godown</GODOWNNAME>
              <BATCHNAME>LOT-2026-A</BATCHNAME>
              <OPENINGBALANCE> 20 pcs</OPENINGBALANCE>
              <OPENINGRATE>2100.00/pcs</OPENINGRATE>
            </BATCHALLOCATIONS.LIST>
          </STOCKITEM>
        </TALLYMESSAGE>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            <DATE>20261115</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>TLY-INV-101</VOUCHERNUMBER>
            <PARTYLEDGERNAME>Sharma Hardware</PARTYLEDGERNAME>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sharma Hardware</LEDGERNAME>
              <AMOUNT>-4956.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>Bosch Angle Grinder GWS 600</STOCKITEMNAME>
              <RATE>2100.00/pcs</RATE>
              <AMOUNT>4200.00</AMOUNT>
              <BILLEDQTY> 2 pcs</BILLEDQTY>
            </ALLINVENTORYENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
"""


class TallyXMLImporterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="tally_importer_tenant",
            email="tallyimp@cenvora.com",
            password="pass",
            business_name="Metro Industrial Tools",
            state="Karnataka",
            gstin="29METRO1234T1Z8",
        )
        self.client.force_authenticate(user=self.tenant)

    def test_parse_utf16_tally_xml_and_commit(self):
        """Verify UTF-16LE XML with &#4; control char parses, validates, and commits."""
        utf16_bytes = SAMPLE_TALLY_XML.encode("utf-16")
        parsed = parse_tally_xml(utf16_bytes)

        self.assertEqual(len(parsed["parties"]), 2)
        self.assertEqual(len(parsed["products"]), 1)
        self.assertEqual(len(parsed["batches"]), 1)
        self.assertEqual(len(parsed["historical_vouchers"]), 1)

        vch = parsed["historical_vouchers"][0]
        self.assertEqual(vch["document_number"], "TLY-INV-101")
        self.assertEqual(vch["document_date"], "2026-11-15")
        self.assertEqual(vch["total_amount"], 4956.0)

        gate = MigrationValidationGate(tenant=self.tenant)
        res = gate.commit(
            products=parsed["products"],
            parties=parsed["parties"],
            batches=parsed["batches"],
        )
        self.assertTrue(res["success"])

        cust = Customer.objects.get(created_by=self.tenant, name="Sharma Hardware")
        self.assertEqual(cust.current_balance, Decimal("15400.00"))
        self.assertEqual(cust.gstin, "29SHARM1234S1Z5")

        vend = Vendor.objects.get(created_by=self.tenant, name="Bosch India Ltd")
        self.assertEqual(vend.gstin, "29BOSCH1234B1Z2")

        prod = Product.objects.get(created_by=self.tenant, item_code="GWS-600")
        self.assertEqual(prod.tax, Decimal("18.00"))
        self.assertEqual(prod.conversion_factor, 4)
        self.assertEqual(prod.stock, Decimal("20.000"))

        batch = ProductBatch.objects.get(product=prod, batch_number="LOT-2026-A")
        self.assertEqual(batch.cost_price, Decimal("2100.0000"))

    def test_tally_xml_api_endpoints(self):
        """Verify POST /api/users/migration/tally-dry-run/ and tally-commit/."""
        utf16_bytes = SAMPLE_TALLY_XML.encode("utf-16")

        dry_file = SimpleUploadedFile("Master.xml", utf16_bytes, content_type="application/xml")
        dry_resp = self.client.post("/api/users/migration/tally-dry-run/", {"file": dry_file}, format="multipart")
        self.assertEqual(dry_resp.status_code, 200)
        self.assertTrue(dry_resp.data["can_commit"])
        self.assertEqual(dry_resp.data["historical_vouchers_count"], 1)

        commit_file = SimpleUploadedFile("Master.xml", utf16_bytes, content_type="application/xml")
        commit_resp = self.client.post("/api/users/migration/tally-commit/", {"file": commit_file}, format="multipart")
        self.assertEqual(commit_resp.status_code, 200)
        self.assertTrue(commit_resp.data["success"])
        self.assertTrue(Product.objects.filter(created_by=self.tenant, item_code="GWS-600").exists())

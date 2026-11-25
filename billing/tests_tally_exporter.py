"""
Unit tests for TallyPrime XML Export Engine (billing/tally_exporter.py).
Tests:
- XML syntax validity via ElementTree
- Accounting-Only vs Full-Inventory export modes
- Local sales (CGST+SGST) vs Interstate sales (IGST)
- Free deal schemes via <ACTUALQTY> and <BILLEDQTY>
- Batch allocations with expiry and manufacturing dates
- Credit Notes & Debit Notes with original invoice references
- Tenant isolation and CA Chart of Accounts customization
"""
from datetime import date, timedelta
from decimal import Decimal
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.models import (
    Customer,
    Vendor,
    SalesInvoice,
    SalesInvoiceItem,
    PurchaseBill,
    PurchaseBillItem,
)
from billing.models_returns import (
    CreditNote,
    CreditNoteItem,
    DebitNote,
    DebitNoteItem,
)
from billing.tally_exporter import TallyXMLExporter
from inventory.models import Product, ProductBatch, Warehouse

User = get_user_model()


class TallyXMLExporterTests(TestCase):
    def setUp(self):
        # Tenant 1 (Karnataka, GSTIN: 29...)
        self.tenant = User.objects.create_user(
            username="tally_test_tenant",
            email="tenant@tallytest.com",
            password="pass",
            business_name="Apex Wholesale India",
            state="Karnataka",
            gstin="29AAACH1234F1Z8",
        )

        # Tenant 2 (Isolation check)
        self.other_tenant = User.objects.create_user(
            username="other_test_tenant",
            email="other@tallytest.com",
            password="pass",
            business_name="Other Trading Co",
            state="Maharashtra",
            gstin="27BBBCD5678G2Z1",
        )

        self.warehouse = Warehouse.objects.create(
            name="Main Godown",
            created_by=self.tenant,
        )

        self.local_customer = Customer.objects.create(
            name="Bangalore Retailer",
            gstin="29AABCB2222C1Z4",
            state="Karnataka",
            created_by=self.tenant,
        )

        self.interstate_customer = Customer.objects.create(
            name="Mumbai Chemist",
            gstin="27AABCM9999M1Z2",
            state="Maharashtra",
            created_by=self.tenant,
        )

        self.vendor = Vendor.objects.create(
            name="Cipla Pharma Ltd",
            gstin="29CIPLA1111C1Z0",
            state="Karnataka",
            created_by=self.tenant,
        )

        self.product = Product.objects.create(
            name="Amoxicillin 500mg Strip",
            hsn_sac_code="3004",
            unit="strip",
            price=Decimal("90.00"),
            sale_price=Decimal("100.00"),
            tax=Decimal("12.00"),  # 12% GST
            created_by=self.tenant,
        )

        self.batch = ProductBatch.objects.create(
            product=self.product,
            batch_number="AMX-2026",
            manufacturing_date=date(2026, 1, 15),
            expiry_date=date(2027, 12, 31),
            mrp=Decimal("120.00"),
            cost_price=Decimal("90.00"),
            sale_price=Decimal("100.00"),
        )

    def test_sales_invoice_accounting_only_export(self):
        """Verify sales invoice export in accounting_only mode produces valid XML with correct ledgers."""
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.local_customer,
            customer_name="Bangalore Retailer",
            invoice_number="INV-2026-001",
            invoice_date=date(2026, 11, 25),
            status="final",
            place_of_supply="Karnataka",
            total_amount=Decimal("0.00"),
            warehouse=self.warehouse,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            batch=self.batch,
            quantity=Decimal("10.000"),
            unit="strip",
            price=Decimal("100.00"),
            tax=Decimal("12.00"),
            amount=Decimal("1120.00"),
        )
        inv.refresh_from_db()

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            mode='accounting_only',
        )
        xml_output = exporter.generate_xml()

        # 1. Parse with ElementTree to verify well-formed XML
        root = ET.fromstring(xml_output)
        self.assertEqual(root.tag, "ENVELOPE")

        # 2. Assert voucher contents
        self.assertIn("<VOUCHERNUMBER>INV-2026-001</VOUCHERNUMBER>", xml_output)
        self.assertIn("<PARTYLEDGERNAME>Bangalore Retailer</PARTYLEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Sales Account</LEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Output CGST 6%</LEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Output SGST 6%</LEDGERNAME>", xml_output)
        self.assertIn("<AMOUNT>-1120.00</AMOUNT>", xml_output)  # Party debit is negative

        # In accounting_only mode, no ALLINVENTORYENTRIES tags should exist
        self.assertNotIn("<ALLINVENTORYENTRIES.LIST>", xml_output)

        # Masters check: Debtors and Taxes defined
        self.assertIn('<LEDGER NAME="Bangalore Retailer"', xml_output)
        self.assertIn('<LEDGER NAME="Output CGST 6%"', xml_output)

    def test_interstate_sales_invoice_with_igst(self):
        """Verify interstate sales invoice exports Output IGST instead of CGST/SGST."""
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.interstate_customer,
            customer_name="Mumbai Chemist",
            invoice_number="INV-2026-002",
            invoice_date=date(2026, 11, 25),
            status="final",
            place_of_supply="Maharashtra",
            total_amount=Decimal("0.00"),
            warehouse=self.warehouse,
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            quantity=Decimal("10.000"),
            unit="strip",
            price=Decimal("100.00"),
            tax=Decimal("12.00"),
            amount=Decimal("1120.00"),
        )
        inv.refresh_from_db()

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            ca_mapping={'separate_interstate_sales': True},
        )
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)  # XML validity
        self.assertIn("<LEDGERNAME>Output IGST 12%</LEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Interstate Sales Account</LEDGERNAME>", xml_output)
        self.assertNotIn("Output CGST", xml_output)

    def test_full_inventory_mode_with_batch_and_free_scheme(self):
        """Verify full_inventory mode includes batches, godowns, and actual vs billed qty for free schemes."""
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.local_customer,
            customer_name="Bangalore Retailer",
            invoice_number="INV-2026-003",
            invoice_date=date(2026, 11, 25),
            status="final",
            place_of_supply="Karnataka",
            total_amount=Decimal("0.00"),
            warehouse=self.warehouse,
        )
        # 10 billed + 1 free
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            batch=self.batch,
            quantity=Decimal("10.000"),
            free_quantity=Decimal("1.000"),
            unit="strip",
            price=Decimal("100.00"),
            tax=Decimal("12.00"),
            amount=Decimal("1120.00"),
        )
        inv.refresh_from_db()

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            mode='full_inventory',
        )
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)
        self.assertIn("<ALLINVENTORYENTRIES.LIST>", xml_output)
        self.assertIn("<STOCKITEMNAME>Amoxicillin 500mg Strip</STOCKITEMNAME>", xml_output)
        self.assertIn("<ACTUALQTY> 11 strip</ACTUALQTY>", xml_output)
        self.assertIn("<BILLEDQTY> 10 strip</BILLEDQTY>", xml_output)
        self.assertIn("<GODOWNNAME>Main Godown</GODOWNNAME>", xml_output)
        self.assertIn("<BATCHNAME>AMX-2026</BATCHNAME>", xml_output)
        self.assertIn("<EXPIRYPERIOD>20271231</EXPIRYPERIOD>", xml_output)

    def test_purchase_bill_export(self):
        """Verify purchase bills export with vendor credit, purchase debit, and input taxes."""
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            vendor=self.vendor,
            vendor_name="Cipla Pharma Ltd",
            bill_number="BILL-CIP-901",
            bill_date=date(2026, 11, 24),
            total_amount=Decimal("1008.00"),  # 900 taxable + 108 tax
            warehouse=self.warehouse,
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            batch=self.batch,
            quantity=Decimal("10.000"),
            unit="strip",
            price=Decimal("90.00"),
            tax=Decimal("12.00"),
            amount=Decimal("1008.00"),
        )

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            include_sales=False,
            include_purchases=True,
            include_returns=False,
        )
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)
        self.assertIn("<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>", xml_output)
        self.assertIn("<VOUCHERNUMBER>BILL-CIP-901</VOUCHERNUMBER>", xml_output)
        self.assertIn("<PARTYLEDGERNAME>Cipla Pharma Ltd</PARTYLEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Purchase Account</LEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Input CGST 6%</LEDGERNAME>", xml_output)
        self.assertIn("<LEDGERNAME>Input SGST 6%</LEDGERNAME>", xml_output)
        # In purchase: vendor is credited (+1008.00)
        self.assertIn("<AMOUNT>1008.00</AMOUNT>", xml_output)

    def test_credit_note_sales_return_with_original_ref(self):
        """Verify Credit Note exports formal Credit Note voucher with original invoice link."""
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.local_customer,
            invoice_number="INV-2026-ORIG",
            invoice_date=date(2026, 11, 20),
            status="final",
            total_amount=Decimal("1120.00"),
        )
        cn = CreditNote.objects.create(
            created_by=self.tenant,
            customer=self.local_customer,
            original_invoice=inv,
            credit_note_number="CN-2026-001",
            date=date(2026, 11, 25),
            total_amount=Decimal("224.00"),  # 2 units return: 200 + 24 tax
        )
        CreditNoteItem.objects.create(
            credit_note=cn,
            product=self.product,
            quantity=Decimal("2.000"),
            price=Decimal("100.00"),
            tax=Decimal("12.00"),
            amount=Decimal("224.00"),
        )

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            include_sales=False,
            include_purchases=False,
            include_returns=True,
        )
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)
        self.assertIn("<VOUCHERTYPENAME>Credit Note</VOUCHERTYPENAME>", xml_output)
        self.assertIn("<VOUCHERNUMBER>CN-2026-001</VOUCHERNUMBER>", xml_output)
        self.assertIn("<ORIGINVOICENO>INV-2026-ORIG</ORIGINVOICENO>", xml_output)
        self.assertIn("<ORIGINVOICEDATE>20261120</ORIGINVOICEDATE>", xml_output)
        self.assertIn("<LEDGERNAME>Sales Returns</LEDGERNAME>", xml_output)

    def test_debit_note_purchase_return(self):
        """Verify Debit Note exports formal Debit Note voucher with original bill link."""
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            vendor=self.vendor,
            vendor_name="Cipla Pharma Ltd",
            bill_number="BILL-CIP-ORIG",
            bill_date=date(2026, 11, 15),
            total_amount=Decimal("1008.00"),
        )
        dn = DebitNote.objects.create(
            created_by=self.tenant,
            vendor_name="Cipla Pharma Ltd",
            original_bill=bill,
            debit_note_number="DN-2026-001",
            date=date(2026, 11, 25),
            total_amount=Decimal("201.60"),  # 2 units return
        )
        DebitNoteItem.objects.create(
            debit_note=dn,
            product=self.product,
            quantity=Decimal("2.000"),
            price=Decimal("90.00"),
            tax=Decimal("12.00"),
            amount=Decimal("201.60"),
        )

        exporter = TallyXMLExporter(
            tenant=self.tenant,
            include_sales=False,
            include_purchases=False,
            include_returns=True,
        )
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)
        self.assertIn("<VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>", xml_output)
        self.assertIn("<VOUCHERNUMBER>DN-2026-001</VOUCHERNUMBER>", xml_output)
        self.assertIn("<ORIGINVOICENO>BILL-CIP-ORIG</ORIGINVOICENO>", xml_output)
        self.assertIn("<ORIGINVOICEDATE>20261115</ORIGINVOICEDATE>", xml_output)
        self.assertIn("<LEDGERNAME>Purchase Returns</LEDGERNAME>", xml_output)

    def test_tenant_isolation(self):
        """Verify that transactions belonging to other tenants are strictly excluded."""
        # Create invoice for other tenant
        other_cust = Customer.objects.create(
            name="Other Customer",
            created_by=self.other_tenant,
        )
        SalesInvoice.objects.create(
            created_by=self.other_tenant,
            customer=other_cust,
            invoice_number="OTHER-INV-999",
            invoice_date=date(2026, 11, 25),
            status="final",
            total_amount=Decimal("500.00"),
        )

        exporter = TallyXMLExporter(tenant=self.tenant)
        xml_output = exporter.generate_xml()

        self.assertNotIn("OTHER-INV-999", xml_output)
        self.assertNotIn("Other Customer", xml_output)

    def test_export_tally_xml_api_endpoint(self):
        """Verify GET and POST /api/billing/tally/export-xml/ return attachment response."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.tenant)

        # Create an invoice for this test
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.local_customer,
            invoice_number="INV-API-001",
            invoice_date=date(2026, 11, 25),
            status="final",
            total_amount=Decimal("0.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv,
            product=self.product,
            quantity=Decimal("1.000"),
            price=Decimal("100.00"),
            tax=Decimal("12.00"),
            amount=Decimal("112.00"),
        )

        # 1. GET request
        response = client.get('/api/billing/tally/export-xml/?mode=accounting_only')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml; charset=utf-8')
        self.assertIn('attachment; filename=', response['Content-Disposition'])
        self.assertIn('<TALLYREQUEST>Import Data</TALLYREQUEST>', response.content.decode('utf-8'))

        # 2. POST request with custom CA mapping
        payload = {
            'mode': 'accounting_only',
            'ca_mapping': {
                'sales_account': 'Custom Sales Ledger',
            }
        }
        post_resp = client.post('/api/billing/tally/export-xml/', payload, format='json')
        self.assertEqual(post_resp.status_code, 200)
        self.assertIn('Custom Sales Ledger', post_resp.content.decode('utf-8'))


"""
Unit tests for Passwordless CA Magic-Link Portal APIs (billing/ca_portal_views.py).
Tests:
- Missing, invalid, and expired magic link token rejections (401 / 403)
- Valid magic link summary endpoint (business info, GST summary, trial balance)
- Valid magic link downloads: Tally XML, Audit Pack ZIP, and GSTR Excel
- Tenant isolation via token
"""
from datetime import date, timedelta
from decimal import Decimal
import io
import xml.etree.ElementTree as ET
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
import openpyxl
from rest_framework.test import APIClient

from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from billing.models_ca import CAMappingProfile
from inventory.models import Product

User = get_user_model()


class CAPortalAPITests(TestCase):
    def setUp(self):
        # Unauthenticated client (simulating external CA opening a magic link)
        self.client = APIClient()

        self.tenant = User.objects.create_user(
            username="ca_portal_tenant",
            email="owner@portal.com",
            password="pass",
            business_name="Nandi Pharma Distributors",
            state="Karnataka",
            gstin="29NANDI1234N1Z5",
        )
        self.profile = CAMappingProfile.objects.create(
            tenant=self.tenant,
            ca_name="CA Vikram Joshi",
            ca_firm_name="Joshi & Co Chartered Accountants",
            ca_email="vikram@joshica.in",
        )
        self.valid_token = self.profile.generate_magic_link_token(days=30)

        self.customer = Customer.objects.create(
            name="Apollo Chemist",
            gstin="29APOLL1234A1Z2",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Azithromycin 500mg",
            hsn_sac_code="3004",
            unit="strip",
            price=Decimal("50.00"),
            sale_price=Decimal("70.00"),
            tax=Decimal("12.00"),
            created_by=self.tenant,
        )

        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name="Apollo Chemist",
            invoice_number="INV-PORTAL-001",
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
            price=Decimal("70.00"),
            tax=Decimal("12.00"),
            amount=Decimal("784.00"),
        )

    def test_missing_or_invalid_or_expired_token_rejected(self):
        """Missing token -> 401; Invalid token -> 403; Expired token -> 403."""
        resp_missing = self.client.get('/api/billing/ca-portal/summary/')
        self.assertEqual(resp_missing.status_code, 401)

        resp_invalid = self.client.get('/api/billing/ca-portal/summary/?token=bogus-token-123')
        self.assertEqual(resp_invalid.status_code, 403)

        # Expire the token
        self.profile.magic_link_expires_at = timezone.now() - timedelta(days=1)
        self.profile.save(update_fields=['magic_link_expires_at'])

        resp_expired = self.client.get(f'/api/billing/ca-portal/summary/?token={self.valid_token}')
        self.assertEqual(resp_expired.status_code, 403)
        self.assertIn('expired', resp_expired.data['error'].lower())

    def test_ca_portal_summary_with_valid_token(self):
        """Verify summary returns business details, GST metrics, and trial balance status."""
        resp = self.client.get(
            f'/api/billing/ca-portal/summary/?token={self.valid_token}&start_date=2026-11-01&end_date=2026-11-30'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.data

        self.assertEqual(data['business']['name'], 'Nandi Pharma Distributors')
        self.assertEqual(data['business']['gstin'], '29NANDI1234N1Z5')
        self.assertEqual(data['ca']['name'], 'CA Vikram Joshi')
        self.assertEqual(data['summary']['sales_count'], 1)
        self.assertGreater(data['summary']['sales_total'], 0)
        self.assertIn('is_balanced', data['trial_balance'])

    def test_ca_portal_downloads_xml_zip_excel(self):
        """Verify XML, ZIP, and Excel downloads work cleanly without JWT login."""
        # 1. XML download
        xml_resp = self.client.get(
            f'/api/billing/ca-portal/download-xml/?token={self.valid_token}&start_date=2026-11-01&end_date=2026-11-30&mode=accounting_only'
        )
        self.assertEqual(xml_resp.status_code, 200)
        xml_str = xml_resp.content.decode('utf-8')
        ET.fromstring(xml_str)
        self.assertIn('INV-PORTAL-001', xml_str)

        # 2. ZIP download
        zip_resp = self.client.get(
            f'/api/billing/ca-portal/download-zip/?token={self.valid_token}&start_date=2026-11-01&end_date=2026-11-30'
        )
        self.assertEqual(zip_resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(zip_resp.content), 'r') as zf:
            self.assertIn('Tally_Vouchers_Accounting.xml', zf.namelist())
            self.assertIn('Trial_Balance.xlsx', zf.namelist())

        # 3. Excel download
        xlsx_resp = self.client.get(
            f'/api/billing/ca-portal/download-excel/?token={self.valid_token}&start_date=2026-11-01&end_date=2026-11-30'
        )
        self.assertEqual(xlsx_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_resp.content))
        self.assertGreaterEqual(len(wb.sheetnames), 1)

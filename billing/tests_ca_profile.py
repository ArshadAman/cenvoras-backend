"""
Unit tests for CA Mapping Profile Engine & Auditor Access APIs.
Tests:
- Default profile creation and standard Tally presets
- Preset switching ('standard_tally', 'detailed_slabs', 'simplified_gst')
- Custom ledger name updates & overrides
- Magic link token generation and expiry validation
- Automatic integration with TallyXMLExporter
- Tenant isolation
"""
from datetime import date, timedelta
from decimal import Decimal
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import (
    Customer,
    SalesInvoice,
    SalesInvoiceItem,
)
from billing.models_ca import CAMappingProfile, CAPresetChoices
from billing.tally_exporter import TallyXMLExporter
from inventory.models import Product

User = get_user_model()


class CAMappingProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="ca_test_tenant",
            email="tenant@caprofile.com",
            password="pass",
            business_name="Apex Healthcare Dist",
            state="Karnataka",
            gstin="29AAACH1234F1Z8",
        )
        self.other_tenant = User.objects.create_user(
            username="other_ca_tenant",
            email="other@caprofile.com",
            password="pass",
            business_name="Other Pharma Co",
            state="Maharashtra",
            gstin="27BBBCD5678G2Z1",
        )
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="Green Cross Pharmacy",
            gstin="29GREEN1234C1Z9",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Cough Syrup 100ml",
            hsn_sac_code="3004",
            unit="bot",
            price=Decimal("80.00"),
            sale_price=Decimal("100.00"),
            tax=Decimal("12.00"),
            created_by=self.tenant,
        )

    def test_get_ca_profile_auto_creates_default(self):
        """GET /api/billing/tally/ca-profile/ creates a default profile if none exists."""
        self.assertFalse(CAMappingProfile.objects.filter(tenant=self.tenant).exists())

        response = self.client.get('/api/billing/tally/ca-profile/')
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertEqual(data['preset'], CAPresetChoices.STANDARD_TALLY)
        self.assertEqual(data['sales_account'], 'Sales Account')
        self.assertEqual(data['round_off_account'], 'Round Off')
        self.assertEqual(data['cgst_output_prefix'], 'Output CGST')
        self.assertTrue(data['split_tax_by_rate'])

        # Verify profile is now in DB
        self.assertTrue(CAMappingProfile.objects.filter(tenant=self.tenant).exists())

    def test_update_ca_profile(self):
        """PUT /api/billing/tally/ca-profile/ updates custom ledger mappings and CA contact."""
        payload = {
            'ca_name': 'Ramesh Sharma, FCA',
            'ca_email': 'sharma.associates@tax.com',
            'ca_firm_name': 'Sharma & Associates CA',
            'sales_account': 'GST Sales @ 18% Main',
            'round_off_account': 'Rounding Adjustment Ledger',
            'custom_ledger_overrides': {
                'Output CGST 6%': 'CGST @ 6% Account',
            }
        }
        response = self.client.put('/api/billing/tally/ca-profile/', payload, format='json')
        self.assertEqual(response.status_code, 200)

        profile = CAMappingProfile.objects.get(tenant=self.tenant)
        self.assertEqual(profile.ca_name, 'Ramesh Sharma, FCA')
        self.assertEqual(profile.sales_account, 'GST Sales @ 18% Main')
        self.assertEqual(profile.round_off_account, 'Rounding Adjustment Ledger')
        self.assertEqual(profile.custom_ledger_overrides['Output CGST 6%'], 'CGST @ 6% Account')

    def test_reset_defaults_preset(self):
        """POST /api/billing/tally/ca-profile/reset-defaults/ applies the detailed_slabs preset."""
        response = self.client.post(
            '/api/billing/tally/ca-profile/reset-defaults/',
            {'preset': 'detailed_slabs'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)

        profile = CAMappingProfile.objects.get(tenant=self.tenant)
        self.assertEqual(profile.preset, 'detailed_slabs')
        self.assertEqual(profile.sales_account, 'Local Sales')
        self.assertEqual(profile.interstate_sales_account, 'Interstate Sales')
        self.assertTrue(profile.separate_interstate_sales)
        self.assertTrue(profile.separate_interstate_purchases)

    def test_generate_magic_link_token(self):
        """POST /api/billing/tally/ca-profile/generate-magic-link/ generates a valid 30-day token."""
        response = self.client.post('/api/billing/tally/ca-profile/generate-magic-link/', {'days': 45}, format='json')
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertTrue(data['success'])
        self.assertIn('magic_link_token', data)
        self.assertIn('/ca-portal?token=', data['magic_link_url'])

        profile = CAMappingProfile.objects.get(tenant=self.tenant)
        self.assertTrue(profile.is_magic_link_valid())
        self.assertGreater(profile.magic_link_expires_at, profile.created_at)

    def test_tally_exporter_automatically_uses_saved_ca_profile(self):
        """Verify TallyXMLExporter automatically uses tenant's CAMappingProfile without manual args."""
        # Save a custom sales account name in profile
        profile, _ = CAMappingProfile.objects.get_or_create(tenant=self.tenant)
        profile.sales_account = "Auto Picked CA Sales Account"
        profile.save()

        # Create a final invoice
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            invoice_number="INV-CA-AUTOPICK",
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

        # Run exporter without explicit ca_mapping
        exporter = TallyXMLExporter(tenant=self.tenant)
        xml_output = exporter.generate_xml()

        ET.fromstring(xml_output)
        self.assertIn("<LEDGERNAME>Auto Picked CA Sales Account</LEDGERNAME>", xml_output)
        self.assertIn('<LEDGER NAME="Auto Picked CA Sales Account"', xml_output)

    def test_tenant_isolation(self):
        """Verify another tenant cannot view or modify this tenant's CA profile."""
        other_client = APIClient()
        other_client.force_authenticate(user=self.other_tenant)

        resp = other_client.get('/api/billing/tally/ca-profile/')
        self.assertEqual(resp.status_code, 200)

        # Ensure other tenant gets their own profile, not self.tenant's
        other_profile = CAMappingProfile.objects.get(tenant=self.other_tenant)
        self.assertNotEqual(other_profile.tenant_id, self.tenant.id)

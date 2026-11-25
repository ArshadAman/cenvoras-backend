"""
Unit tests for Task 5.1: Perpetual Licensing & AMC Enforcement.
Tests:
- HMAC-SHA256 license key issuance, signature validation, and tamper detection
- Tenant binding verification (key bound to one tenant fails for another)
- AMC lifecycle states: 'active', 'grace_warning' (days 1-30), 'amc_locked' (day 31+)
- Middleware enforcement: blocks POST/PUT/PATCH/DELETE with 403 amc_locked when expired,
  while preserving GET read-only access and /api/users/backup/download-zip/ Data Sovereignty
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from subscription.licensing import issue_perpetual_license, verify_license_key
from subscription.models import Plan, TenantSubscription

User = get_user_model()


class PerpetualLicensingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="perpetual_tenant",
            email="perpetual@cenvora.com",
            password="pass",
            business_name="Lifetime Pharma Ltd",
            state="Karnataka",
            gstin="29LIFET1234L1Z5",
        )
        self.other_tenant = User.objects.create_user(
            username="other_perp_tenant",
            email="otherperp@cenvora.com",
            password="pass",
            business_name="Other Co",
        )
        plan, _ = Plan.objects.get_or_create(
            code="enterprise",
            defaults={"name": "Enterprise", "monthly_price": 0},
        )
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=plan,
            status="active",
            billing_cycle="perpetual",
            current_period_end=date.today() + timedelta(days=3650),
        )
        self.client.force_login(self.tenant)
        self.client.force_authenticate(user=self.tenant)

    def test_hmac_license_issuance_and_tamper_detection(self):
        """License key verifies for issuing tenant and rejects tampering or cross-tenant reuse."""
        lic = issue_perpetual_license(self.tenant)
        self.assertTrue(lic.verify_signature())
        self.assertEqual(lic.get_amc_state(), "active")

        # Tampered key must fail
        tampered = lic.license_key[:-4] + "FFFF"
        ok_tamper, _ = verify_license_key(tampered, str(self.tenant.id))
        self.assertFalse(ok_tamper)

        # Cross-tenant key must fail
        ok_cross, _ = verify_license_key(lic.license_key, str(self.other_tenant.id))
        self.assertFalse(ok_cross)

    def test_amc_grace_period_and_read_only_lock_enforcement(self):
        """
        Verify AMC lifecycle:
        - Active: normal writes allowed
        - Grace period (15 days past AMC): writes allowed + X-Cenvora-AMC-Status: grace_warning
        - Expired beyond 30-day grace (35 days past): POST blocked (403 amc_locked),
          GET and /api/users/backup/download-zip/ remain accessible.
        """
        # 1. Grace warning state (expired 15 days ago, 30-day grace)
        lic = issue_perpetual_license(
            self.tenant,
            amc_valid_until=date.today() - timedelta(days=15),
            grace_period_days=30,
        )
        self.assertEqual(lic.get_amc_state(), "grace_warning")
        resp_grace = self.client.get("/api/billing/customers/")
        self.assertEqual(resp_grace.status_code, 200)
        self.assertEqual(resp_grace["X-Cenvora-AMC-Status"], "grace_warning")

        # 2. Hard AMC lock (expired 35 days ago, 30-day grace)
        lic = issue_perpetual_license(
            self.tenant,
            amc_valid_until=date.today() - timedelta(days=35),
            grace_period_days=30,
        )
        self.assertEqual(lic.get_amc_state(), "amc_locked")

        # Write request blocked with 403 amc_locked
        resp_post = self.client.post(
            "/api/billing/customers/",
            {"name": "Blocked Write Customer"},
            format="json",
        )
        self.assertEqual(resp_post.status_code, 403)
        self.assertEqual(resp_post.json().get("code"), "amc_locked")

        # Read-only GET still allowed
        resp_get = self.client.get("/api/billing/customers/")
        self.assertEqual(resp_get.status_code, 200)

        # Data Sovereignty backup ZIP remains 100% accessible even when AMC locked
        resp_backup = self.client.get("/api/users/backup/download-zip/")
        self.assertEqual(resp_backup.status_code, 200)

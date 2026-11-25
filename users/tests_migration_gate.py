"""
Unit tests for 3-Stage Pre-Flight Validation Gate (users/migration_service.py).
Tests:
- Stage 1: Currency noise stripping, MM/YY last-day snap, 10*10 packing extraction, GSTIN & phone cleanup
- Stage 2: In-memory dry-run simulation (zero DB writes, blocker classification, warning classification)
- Stage 3: Atomic database commit + MigrationBlockerError + 100% rollback on mid-flight error
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from billing.models import Customer, Vendor
from inventory.models import Product, ProductBatch, StockPoint, Warehouse
from users.migration_service import (
    MigrationBlockerError,
    MigrationValidationGate,
    parse_marg_expiry,
    parse_marg_packing,
    sanitize_decimal,
    sanitize_gstin,
    sanitize_phone,
)

User = get_user_model()


class MigrationValidationGateTests(TestCase):
    def setUp(self):
        self.tenant = User.objects.create_user(
            username="migr_gate_tenant",
            email="gate@cenvora.com",
            password="pass",
            business_name="Gate Pharma Traders",
            state="Karnataka",
            gstin="29GATEP1234G1Z2",
        )
        self.gate = MigrationValidationGate(tenant=self.tenant)

    def test_stage1_sanitizers_and_marg_parsers(self):
        """Verify decimal cleaning, MM/YY expiry snap, packing parser, GSTIN, and phone cleanup."""
        val, warn = sanitize_decimal("₹ 1,250.50/-")
        self.assertEqual(val, Decimal("1250.50"))
        self.assertIsNotNone(warn)

        # Leap year vs non-leap year last-day snap
        exp_feb_2024, _, err1 = parse_marg_expiry("02/24")
        self.assertFalse(err1)
        self.assertEqual(exp_feb_2024, date(2024, 2, 29))

        exp_nov_2026, _, err2 = parse_marg_expiry("11/26")
        self.assertFalse(err2)
        self.assertEqual(exp_nov_2026, date(2026, 11, 30))

        # Packing parser
        pack = parse_marg_packing("10*10")
        self.assertEqual(pack["packing"], "10x10")
        self.assertEqual(pack["unit"], "strip")
        self.assertEqual(pack["secondary_unit"], "box")
        self.assertEqual(pack["conversion_factor"], 10)

        # GSTIN & Phone
        gstin, gw = sanitize_gstin("29abcde1234f1z5")
        self.assertEqual(gstin, "29ABCDE1234F1Z5")
        self.assertIsNone(gw)

        bad_gstin, bgw = sanitize_gstin("INVALID123")
        self.assertEqual(bad_gstin, "")
        self.assertIsNotNone(bgw)

        phone, pw = sanitize_phone("+91 98765-43210")
        self.assertEqual(phone, "9876543210")
        self.assertIsNone(pw)

    def test_stage2_dry_run_writes_nothing_and_catches_blockers(self):
        """Dry run must not touch the database and must flag duplicate SKU tax conflicts."""
        products = [
            {"name": "Dolo 650", "item_code": "SKU-01", "tax": "12%", "price": "₹ 20.00", "packing": "10*15"},
            {"name": "Dolo 650 Dup", "item_code": "SKU-01", "tax": "18%", "price": "22.00"},  # Blocker: conflicting GST%
        ]
        batches = [
            {"product_name": "Dolo 650", "batch_number": "DL-99", "expiry_date": "11/26", "quantity": "-5"},  # Clamped warning
            {"product_name": "Dolo 650", "batch_number": "DL-BAD", "expiry_date": "99/26", "quantity": "10"},  # Blocker: invalid month
        ]

        report = self.gate.dry_run(products=products, batches=batches)
        self.assertFalse(report.can_commit)
        self.assertEqual(report.blocker_count, 2)
        self.assertGreaterEqual(report.auto_fixed_count, 1)

        # Ensure zero DB rows were created
        self.assertEqual(Product.objects.filter(created_by=self.tenant).count(), 0)
        self.assertEqual(ProductBatch.objects.count(), 0)

        # Attempting to commit with blockers must raise MigrationBlockerError
        with self.assertRaises(MigrationBlockerError):
            self.gate.commit(dry_run_report=report)

    def test_stage3_atomic_commit_success(self):
        """Clean/sanitized payload commits Products, Parties, Batches, and StockPoints atomically."""
        products = [
            {
                "name": "Augmentin 625 Duo",
                "item_code": "AUG-625",
                "hsn_sac_code": "3004",
                "manufacturer": "GSK",
                "packing": "10*10",
                "price": "₹ 150.00",
                "sale_price": "₹ 185.00",
                "tax": "12%",
            }
        ]
        parties = [
            {
                "name": "Lifeline Medicals",
                "party_type": "customer",
                "gstin": "29LIFEL1234L1Z9",
                "phone": "+91 9988776655",
                "state": "Karnataka",
                "opening_balance": "12,500.00",
            },
            {
                "name": "GSK Distributors",
                "party_type": "vendor",
                "gstin": "29GSKDI1234G1Z1",
                "state": "Karnataka",
            },
        ]
        batches = [
            {
                "product_name": "Augmentin 625 Duo",
                "item_code": "AUG-625",
                "batch_number": "AG-2026",
                "expiry_date": "02/27",
                "warehouse_name": "Pharma Main Godown",
                "quantity": "150",
                "mrp": "220.00",
                "cost_price": "150.00",
                "sale_price": "185.00",
            }
        ]

        result = self.gate.commit(products=products, parties=parties, batches=batches)
        self.assertTrue(result["success"])
        self.assertEqual(result["created"]["products"], 1)
        self.assertEqual(result["created"]["customers"], 1)
        self.assertEqual(result["created"]["vendors"], 1)
        self.assertEqual(result["created"]["batches"], 1)

        prod = Product.objects.get(created_by=self.tenant, item_code="AUG-625")
        self.assertEqual(prod.unit, "strip")
        self.assertEqual(prod.secondary_unit, "box")
        self.assertEqual(prod.conversion_factor, 10)
        self.assertEqual(prod.stock, Decimal("150.000"))

        batch = ProductBatch.objects.get(product=prod, batch_number="AG-2026")
        self.assertEqual(batch.expiry_date, date(2027, 2, 28))

        cust = Customer.objects.get(created_by=self.tenant, name="Lifeline Medicals")
        self.assertEqual(cust.current_balance, Decimal("12500.00"))
        self.assertEqual(cust.phone, "9988776655")

    def test_stage3_atomic_rollback_on_db_error(self):
        """If an unexpected error occurs mid-commit, zero partial rows remain in DB."""
        products = [{"name": "Rollback Item", "item_code": "RB-01", "tax": "12", "price": "50"}]
        batches = [{"product_name": "Rollback Item", "item_code": "RB-01", "batch_number": "B1", "quantity": "10"}]

        with patch.object(StockPoint.objects, "update_or_create", side_effect=RuntimeError("Simulated DB crash")):
            with self.assertRaises(RuntimeError):
                self.gate.commit(products=products, batches=batches)

        # Product must have been rolled back!
        self.assertEqual(Product.objects.filter(created_by=self.tenant, item_code="RB-01").count(), 0)
        self.assertEqual(Warehouse.objects.filter(created_by=self.tenant).count(), 0)

from datetime import date
from decimal import Decimal
from rest_framework import status
from billing.models import Customer, SalesInvoice, Payment
from billing.models_sidecar import DeliveryChallan, PartyMeta
from billing.services_customer_merge import merge_customers, find_duplicate_customers
from billing.tests_four_fixes import FourFixesBase
from billing.serializers import CustomerSerializer


class CustomerMergeAndDeduplicationTests(FourFixesBase):
    def test_duplicate_gstin_rejected_in_customer_serializer(self):
        """CustomerSerializer must reject creating a customer with an existing GSTIN."""
        valid_gstin = "21AAATJ3251E2ZA"
        Customer.objects.create(
            name="JITM Primary",
            gstin=valid_gstin,
            created_by=self.tenant,
        )

        # Attempt to create duplicate via serializer
        payload = {
            "name": "JITM Duplicate",
            "gstin": valid_gstin,
        }
        res = self.client.post("/api/billing/customers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("gstin", str(res.data).lower())
        self.assertIn("already exists", str(res.data).lower())

    def test_duplicate_gstin_case_insensitive_and_whitespace(self):
        """Case variations and spaces in GSTIN must be caught by duplicate validation."""
        valid_gstin = "21AAATJ3251E2ZA"
        Customer.objects.create(
            name="JITM Primary",
            gstin=valid_gstin,
            created_by=self.tenant,
        )

        payload = {
            "name": "JITM Another",
            "gstin": "  21aaatj3251e2za  ",
        }
        res = self.client.post("/api/billing/customers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_blank_gstin_allowed_for_multiple_unregistered_customers(self):
        """Unregistered walk-in customers with blank GSTIN must not be blocked."""
        c1 = Customer.objects.create(name="Walkin 1", gstin="", created_by=self.tenant)
        payload = {"name": "Walkin 2", "gstin": ""}
        res = self.client.post("/api/billing/customers/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_merge_customers_repoints_all_transactions_and_recomputes_balance(self):
        """Merging duplicate customer into primary must repoint all docs and recalculate balance."""
        gstin = "21AAATJ3251E2ZA"
        primary = Customer.objects.create(
            name="JITM",
            gstin=gstin,
            created_by=self.tenant,
        )
        duplicate = Customer.objects.create(
            name="JITM",
            gstin=gstin,
            phone="9876543210",
            address="Paralakhemundi, Odisha",
            created_by=self.tenant,
        )

        PartyMeta.objects.create(
            customer=duplicate,
            shipping_addresses=["Site Office, Gajapati, 761211"],
            loyalty_points=50,
        )

        # Invoice 1 on primary: 1,000
        inv1 = SalesInvoice.objects.create(
            invoice_number="INV-P-001",
            invoice_date=date.today(),
            customer=primary,
            customer_name=primary.name,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0.00"),
            status="final",
            created_by=self.tenant,
        )
        # Invoice 2 on duplicate: 2,000
        inv2 = SalesInvoice.objects.create(
            invoice_number="INV-D-002",
            invoice_date=date.today(),
            customer=duplicate,
            customer_name=duplicate.name,
            total_amount=Decimal("2000.00"),
            amount_paid=Decimal("500.00"),
            status="final",
            created_by=self.tenant,
        )
        # Payment on duplicate: 500
        pay = Payment.objects.create(
            customer=duplicate,
            invoice=inv2,
            amount=Decimal("500.00"),
            date=date.today(),
            created_by=self.tenant,
        )

        # Challan on duplicate
        dc = DeliveryChallan.objects.create(
            challan_number="DC-D-001",
            date=date.today(),
            customer=duplicate,
            customer_name=duplicate.name,
            total_amount=Decimal("0.00"),
            created_by=self.tenant,
        )

        dup_id = duplicate.id

        # Execute merge
        res = merge_customers(self.tenant, primary.id, duplicate.id, actor=self.tenant)
        self.assertEqual(res['primary_customer_id'], str(primary.id))
        self.assertEqual(res['deleted_duplicate_id'], str(dup_id))

        # Duplicate customer must be deleted
        self.assertFalse(Customer.objects.filter(id=dup_id).exists())

        # Primary customer fields backfilled
        primary.refresh_from_db()
        self.assertEqual(primary.phone, "9876543210")
        self.assertEqual(primary.address, "Paralakhemundi, Odisha")

        # PartyMeta merged
        p_meta = PartyMeta.objects.get(customer=primary)
        self.assertIn("Site Office, Gajapati, 761211", p_meta.shipping_addresses)
        self.assertEqual(p_meta.loyalty_points, 50)

        # All documents reassigned
        inv2.refresh_from_db()
        self.assertEqual(inv2.customer_id, primary.id)

        pay.refresh_from_db()
        self.assertEqual(pay.customer_id, primary.id)

        dc.refresh_from_db()
        self.assertEqual(dc.customer_id, primary.id)

        # Primary balance must be 1,000 + (2,000 - 500) = 2,500.00
        self.assertEqual(primary.current_balance, Decimal("2500.00"))

    def test_invoice_filtering_does_not_leak_across_same_name_customers(self):
        """Querying invoices for customer A must not return customer B's invoices even if names match."""
        cust_a = Customer.objects.create(name="SameName Corp", created_by=self.tenant)
        cust_b = Customer.objects.create(name="SameName Corp", created_by=self.tenant)

        inv_a = SalesInvoice.objects.create(
            invoice_number="INV-A-101",
            invoice_date=date.today(),
            customer=cust_a,
            customer_name=cust_a.name,
            total_amount=Decimal("100.00"),
            status="final",
            created_by=self.tenant,
        )
        inv_b = SalesInvoice.objects.create(
            invoice_number="INV-B-202",
            invoice_date=date.today(),
            customer=cust_b,
            customer_name=cust_b.name,
            total_amount=Decimal("200.00"),
            status="final",
            created_by=self.tenant,
        )

        res_a = self.client.get(f"/api/billing/sales-invoices/?customer={cust_a.id}")
        self.assertEqual(res_a.status_code, status.HTTP_200_OK)
        numbers_a = [inv["invoice_number"] for inv in res_a.data]
        self.assertIn("INV-A-101", numbers_a)
        self.assertNotIn("INV-B-202", numbers_a)

        res_b = self.client.get(f"/api/billing/sales-invoices/?customer={cust_b.id}")
        self.assertEqual(res_b.status_code, status.HTTP_200_OK)
        numbers_b = [inv["invoice_number"] for inv in res_b.data]
        self.assertIn("INV-B-202", numbers_b)
        self.assertNotIn("INV-A-101", numbers_b)

    def test_merge_api_endpoint(self):
        """POST /api/billing/customers/merge/ successfully merges via HTTP."""
        c1 = Customer.objects.create(name="Primary Co", gstin="21AAATJ3251E2ZA", created_by=self.tenant)
        c2 = Customer.objects.create(name="Duplicate Co", gstin="21AAATJ3251E2ZA", created_by=self.tenant)

        res = self.client.post("/api/billing/customers/merge/", {
            "primary_id": str(c1.id),
            "duplicate_id": str(c2.id),
        }, format="json")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["success"])
        self.assertFalse(Customer.objects.filter(id=c2.id).exists())

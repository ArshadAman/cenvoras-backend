"""
Tests for the four accounting/UI fixes:
1. Invoice HSN validation (readable path + no orphan product)
2. Sales Order GET/PUT/convert fields
3. Debit note GST reversal + GSTR-3B 4(B)/4(C) ledger alignment + repair idempotency
"""
from decimal import Decimal
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from billing.models import SalesInvoice, SalesInvoiceItem, Customer, Vendor, PurchaseBill, PurchaseBillItem
from billing.models_returns import DebitNote, DebitNoteItem
from billing.models_sidecar import SalesOrder, SalesOrderItem
from billing.debit_note_gst_repair import repair_debit_note_gst_reversals, debit_note_needs_gst_repair
from inventory.models import Product
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService
from subscription.models import Plan, TenantSubscription

User = get_user_model()


class FourFixesBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="four_fixes_tenant",
            email="four@cenvora.com",
            password="securepassword123",
            business_name="Four Fixes Pvt Ltd",
            state="Karnataka",
            gstin="29XYZAB1234C1Z1",
        )
        plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro", "monthly_price": 0})
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=plan,
            status="active",
            current_period_end=date.today() + timedelta(days=365),
        )
        self.client.force_authenticate(user=self.tenant)

        self.customer = Customer.objects.create(
            name="KA Buyer",
            gstin="29ABCDE1234F1Z5",
            state="Karnataka",
            created_by=self.tenant,
        )
        self.product = Product.objects.create(
            name="Widget",
            hsn_sac_code="847130",
            sale_price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            unit="pcs",
            stock=100,
            created_by=self.tenant,
        )


class InvoiceHSNValidationTests(FourFixesBase):
    def test_missing_hsn_rejects_and_creates_nothing(self):
        before_products = Product.objects.filter(created_by=self.tenant).count()
        before_invoices = SalesInvoice.objects.filter(created_by=self.tenant).count()
        before_items = SalesInvoiceItem.objects.filter(sales_invoice__created_by=self.tenant).count()

        payload = {
            "customer_name": self.customer.name,
            "invoice_date": str(date.today()),
            "status": "final",
            "total_amount": "118.00",
            "items": [
                {
                    "product": "R5 NoHSN Line X",
                    "quantity": 1,
                    "price": "100.00",
                    "tax": "18.00",
                    "amount": "118.00",
                    "hsn_sac_code": "",
                }
            ],
        }
        res = self.client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("details", res.data)
        items_err = res.data["details"].get("items")
        self.assertTrue(items_err)
        # Nested HSN error present
        flat = str(items_err)
        self.assertIn("hsn_sac_code", flat.lower().replace("/", "_") if False else flat)
        self.assertTrue(
            any(
                (isinstance(row, dict) and "hsn_sac_code" in row)
                for row in items_err
                if isinstance(row, dict)
            )
        )

        self.assertEqual(Product.objects.filter(created_by=self.tenant).count(), before_products)
        self.assertEqual(SalesInvoice.objects.filter(created_by=self.tenant).count(), before_invoices)
        self.assertEqual(
            SalesInvoiceItem.objects.filter(sales_invoice__created_by=self.tenant).count(),
            before_items,
        )
        self.assertFalse(Product.objects.filter(name="R5 NoHSN Line X", created_by=self.tenant).exists())


class SalesOrderFourFixesTests(FourFixesBase):
    def test_put_get_and_convert_carry_hsn_gstin(self):
        create_payload = {
            "order_number": "SO-FF-001",
            "date": str(date.today()),
            "customer_name": self.customer.name,
            "customer_gstin": self.customer.gstin,
            "place_of_supply": "Karnataka",
            "total_amount": "1180.00",
            "bank_account_id": "bank-1",
            "template_id": "tmpl-1",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 1,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "1180.00",
                    "hsn_sac_code": "847130",
                    "unit": "pcs",
                }
            ],
        }
        create_res = self.client.post("/api/billing/sales-orders/", create_payload, format="json")
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED, create_res.data)
        order_id = create_res.data["id"]

        get_res = self.client.get(f"/api/billing/sales-orders/{order_id}/")
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertEqual(get_res.data.get("customer_gstin"), "29ABCDE1234F1Z5")
        self.assertEqual(get_res.data["items"][0].get("hsn_sac_code"), "847130")

        put_payload = {
            "order_number": "SO-FF-001",
            "date": str(date.today()),
            "customer_name": self.customer.name,
            "customer_gstin": self.customer.gstin,
            "place_of_supply": "Karnataka",
            "total_amount": "2360.00",
            "items": [
                {
                    "product": str(self.product.id),
                    "quantity": 2,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "2360.00",
                    "hsn_sac_code": "847130",
                    "unit": "pcs",
                }
            ],
        }
        put_res = self.client.put(f"/api/billing/sales-orders/{order_id}/", put_payload, format="json")
        self.assertEqual(put_res.status_code, status.HTTP_200_OK, put_res.data)
        self.assertEqual(Decimal(str(put_res.data["total_amount"])), Decimal("2360.00"))
        self.assertEqual(put_res.data["items"][0]["quantity"], 2)
        self.assertEqual(put_res.data["items"][0].get("hsn_sac_code"), "847130")

        convert_res = self.client.post(f"/api/billing/sales-orders/{order_id}/convert_to_invoice/")
        self.assertEqual(convert_res.status_code, status.HTTP_200_OK, convert_res.data)
        invoice = SalesInvoice.objects.get(id=convert_res.data["invoice_id"])
        self.assertEqual(invoice.place_of_supply, "Karnataka")
        line = invoice.items.first()
        self.assertEqual(line.hsn_sac_code, "847130")
        self.assertEqual(Decimal(str(line.tax)), Decimal("18.00"))

    def test_convert_allows_negative_product_stock(self):
        """SO→invoice must succeed when product stock is already negative (oversell)."""
        self.product.stock = -2
        self.product.save(update_fields=["stock"])

        order = SalesOrder.objects.create(
            order_number="R5-SO2-NEG",
            date=date.today(),
            customer=self.customer,
            customer_name=self.customer.name,
            customer_gstin=self.customer.gstin,
            place_of_supply="Karnataka",
            total_amount=Decimal("1180.00"),
            created_by=self.tenant,
        )
        SalesOrderItem.objects.create(
            sales_order=order,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
            unit="pcs",
        )

        convert_res = self.client.post(f"/api/billing/sales-orders/{order.id}/convert_to_invoice/")
        self.assertEqual(convert_res.status_code, status.HTTP_200_OK, convert_res.data)
        self.assertTrue(convert_res.data.get("invoice_id"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, -3)


class DebitNoteGSTAndGSTR3BTests(FourFixesBase):
    def test_interstate_debit_note_posts_igst_reversal(self):
        dn = DebitNote.objects.create(
            created_by=self.tenant,
            debit_note_number="DN-FF-INTER",
            date=date.today(),
            vendor_name="MH Supplier",
            vendor_gstin="27ABCDE1234F1Z5",
            reason="return",
            total_amount=Decimal("2000.00"),
        )
        DebitNoteItem.objects.create(
            debit_note=dn,
            product=self.product,
            quantity=1,
            price=Decimal("2000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("2360.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_debit_note_entries(dn)
        dn.refresh_from_db()

        entries = GeneralLedgerEntry.objects.filter(debit_note=dn)
        ap = entries.filter(account__code='2001').aggregate(s=Sum('debit'))['s'] or Decimal('0')
        purchases = entries.filter(account__code__in=['6001', '6002']).aggregate(s=Sum('credit'))['s'] or Decimal('0')
        igst = entries.filter(account__code='1303').aggregate(s=Sum('credit'))['s'] or Decimal('0')

        self.assertEqual(ap, Decimal('2360.00'))
        self.assertEqual(purchases, Decimal('2000.00'))
        self.assertEqual(igst, Decimal('360.00'))

    def test_intrastate_debit_note_posts_cgst_sgst_reversal(self):
        dn = DebitNote.objects.create(
            created_by=self.tenant,
            debit_note_number="DN-FF-INTRA",
            date=date.today(),
            vendor_name="KA Supplier",
            vendor_gstin="29AAAAA1234A1Z5",
            reason="return",
            total_amount=Decimal("2000.00"),
        )
        DebitNoteItem.objects.create(
            debit_note=dn,
            product=self.product,
            quantity=1,
            price=Decimal("2000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("2360.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_debit_note_entries(dn)

        entries = GeneralLedgerEntry.objects.filter(debit_note=dn)
        cgst = entries.filter(account__code='1301').aggregate(s=Sum('credit'))['s'] or Decimal('0')
        sgst = entries.filter(account__code='1302').aggregate(s=Sum('credit'))['s'] or Decimal('0')
        igst = entries.filter(account__code='1303').aggregate(s=Sum('credit'))['s'] or Decimal('0')

        self.assertEqual(cgst, Decimal('180.00'))
        self.assertEqual(sgst, Decimal('180.00'))
        self.assertEqual(igst, Decimal('0.00'))

    def test_repair_dn0001_style_and_idempotent(self):
        # Simulate legacy DN with AP=Purchases only (no GST credits)
        dn = DebitNote.objects.create(
            created_by=self.tenant,
            debit_note_number="DN-0001",
            date=date.today(),
            vendor_name="Legacy MH",
            vendor_gstin="27ABCDE1234F1Z5",
            reason="return",
            total_amount=Decimal("2000.00"),
        )
        DebitNoteItem.objects.create(
            debit_note=dn,
            product=self.product,
            quantity=1,
            price=Decimal("2000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("2360.00"),
            hsn_sac_code="847130",
        )
        accounts = AccountingService.get_or_create_default_accounts(self.tenant)
        GeneralLedgerEntry.objects.create(
            date=dn.date,
            account=accounts['accounts_payable'],
            debit=Decimal('2000.00'),
            credit=0,
            description="legacy",
            reference=dn.debit_note_number,
            debit_note=dn,
            created_by=self.tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=dn.date,
            account=accounts.get('purchase_return') or accounts['purchases'],
            debit=0,
            credit=Decimal('2000.00'),
            description="legacy",
            reference=dn.debit_note_number,
            debit_note=dn,
            created_by=self.tenant,
        )
        self.assertTrue(debit_note_needs_gst_repair(dn))

        repaired, _skipped = repair_debit_note_gst_reversals()
        self.assertGreaterEqual(repaired, 1)
        self.assertFalse(debit_note_needs_gst_repair(dn))

        igst = GeneralLedgerEntry.objects.filter(
            debit_note=dn, account__code='1303'
        ).aggregate(s=Sum('credit'))['s'] or Decimal('0')
        ap = GeneralLedgerEntry.objects.filter(
            debit_note=dn, account__code='2001'
        ).aggregate(s=Sum('debit'))['s'] or Decimal('0')
        self.assertEqual(igst, Decimal('360.00'))
        self.assertEqual(ap, Decimal('2360.00'))

        entries_before = GeneralLedgerEntry.objects.filter(debit_note=dn).count()
        repaired2, skipped2 = repair_debit_note_gst_reversals()
        self.assertEqual(repaired2, 0)
        self.assertGreaterEqual(skipped2, 1)
        self.assertEqual(GeneralLedgerEntry.objects.filter(debit_note=dn).count(), entries_before)

    def test_gstr3b_4b_and_4c_match_ledger_itc(self):
        # Purchase bill ITC (inter-state)
        vendor = Vendor.objects.create(
            name="MH Vendor",
            gstin="27XYZAB9999C1Z1",
            state="Maharashtra",
            created_by=self.tenant,
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number="PB-FF-001",
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            total_amount=Decimal("1180.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)

        dn = DebitNote.objects.create(
            created_by=self.tenant,
            debit_note_number="DN-FF-3B",
            date=date.today(),
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            reason="return",
            total_amount=Decimal("1180.00"),
        )
        DebitNoteItem.objects.create(
            debit_note=dn,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_debit_note_entries(dn)

        ledger_itc_igst = GeneralLedgerEntry.objects.filter(
            created_by=self.tenant, account__code='1303', purchase_bill__isnull=False, debit__gt=0,
        ).aggregate(s=Sum('debit'))['s'] or Decimal('0')
        ledger_rev_igst = GeneralLedgerEntry.objects.filter(
            created_by=self.tenant, account__code='1303', debit_note__isnull=False, credit__gt=0,
        ).aggregate(s=Sum('credit'))['s'] or Decimal('0')
        ledger_net = ledger_itc_igst - ledger_rev_igst

        res = self.client.get(f"/api/billing/gst/gstr3b/?from={date.today()}&to={date.today()}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        t4 = res.data['table_4_itc']
        self.assertEqual(Decimal(str(t4['itc_available']['all_other_itc']['iamt'])), ledger_itc_igst)
        self.assertEqual(Decimal(str(t4['itc_reversed']['others']['iamt'])), ledger_rev_igst)
        self.assertEqual(Decimal(str(t4['net_itc_available']['iamt'])), max(ledger_net, Decimal('0')))

class PurchaseGSTAndVendorComplianceTests(FourFixesBase):
    def test_interstate_purchase_posts_input_igst_when_vendor_state_blank(self):
        """MH vendor GSTIN + empty vendor.state against KA buyer ? Input IGST ?1,800."""
        vendor = Vendor.objects.create(
            name="MH Supplier Blank State",
            gstin="27ABCDE1234F1Z5",
            state="",  # must not force CGST/SGST
            created_by=self.tenant,
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number="PB-IGST-001",
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            total_amount=Decimal("11800.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=1,
            price=Decimal("10000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)
        igst = GeneralLedgerEntry.objects.filter(
            purchase_bill=bill, account__code="1303", debit__gt=0
        ).aggregate(s=Sum("debit"))["s"] or Decimal("0")
        cgst = GeneralLedgerEntry.objects.filter(
            purchase_bill=bill, account__code="1301", debit__gt=0
        ).aggregate(s=Sum("debit"))["s"] or Decimal("0")
        self.assertEqual(igst, Decimal("1800.00"))
        self.assertEqual(cgst, Decimal("0.00"))

    def test_duplicate_vendor_gstin_rejected(self):
        Vendor.objects.create(
            name="V1", gstin="27AAAAA0000A1Z5", created_by=self.tenant
        )
        res = self.client.post(
            "/api/billing/vendors/",
            {"name": "V2", "gstin": "27AAAAA0000A1Z5"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_vendor_payment_reduces_bill_balance(self):
        from ledger.services import AccountingService as AS
        bank = AS.get_or_create_default_accounts(self.tenant).get("1002")
        # Fund bank so balance check paths elsewhere stay happy
        GeneralLedgerEntry.objects.create(
            date=date.today(),
            account=bank,
            debit=Decimal("50000.00"),
            credit=Decimal("0.00"),
            description="Seed",
            reference="SEED-BANK",
            created_by=self.tenant,
        )
        vendor = Vendor.objects.create(
            name="Pay Vendor", gstin="29PAYEE1234F1Z5", state="Karnataka", created_by=self.tenant
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number="PB-PAY-001",
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            total_amount=Decimal("1180.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)
        res = self.client.post(
            "/api/billing/vendor-payments/",
            {
                "bill_id": str(bill.id),
                "amount": "1180.00",
                "payment_account_id": str(bank.id),
                "mode": "bank_transfer",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        bill.refresh_from_db()
        self.assertEqual(bill.payment_status, "paid")
        self.assertEqual(Decimal(str(bill.amount_paid)), Decimal("1180.00"))

    def test_vendor_payment_tds_194c_on_taxable_not_gst_inclusive(self):
        """194C @ 2% must use GST-exclusive taxable (₹20 on ₹1,000), not ₹23.60 on ₹1,180."""
        from ledger.services import AccountingService as AS
        bank = AS.get_or_create_default_accounts(self.tenant).get("1002")
        GeneralLedgerEntry.objects.create(
            date=date.today(),
            account=bank,
            debit=Decimal("50000.00"),
            credit=Decimal("0.00"),
            description="Seed",
            reference="SEED-BANK-TDS",
            created_by=self.tenant,
        )
        vendor = Vendor.objects.create(
            name="TDS Vendor", gstin="29TDSVE1234F1Z5", state="Karnataka", created_by=self.tenant
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number="PB-TDS-001",
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            total_amount=Decimal("1180.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)
        res = self.client.post(
            "/api/billing/vendor-payments/",
            {
                "bill_id": str(bill.id),
                "amount": "1180.00",
                "payment_account_id": str(bank.id),
                "mode": "bank_transfer",
                "tds_section": "194C",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(Decimal(str(res.data["tds_amount"])), Decimal("20.00"))

    def test_vendor_payment_blocks_overdraft(self):
        from ledger.services import AccountingService as AS
        bank = AS.get_or_create_default_accounts(self.tenant).get("1002")
        # Leave bank at ~₹0 (no seed debit)
        vendor = Vendor.objects.create(
            name="OD Vendor", gstin="29ODVEN1234F1Z5", state="Karnataka", created_by=self.tenant
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant,
            bill_number="PB-OD-001",
            bill_date=date.today(),
            vendor=vendor,
            vendor_name=vendor.name,
            vendor_gstin=vendor.gstin,
            total_amount=Decimal("1180.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill,
            product=self.product,
            quantity=1,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("1180.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)
        res = self.client.post(
            "/api/billing/vendor-payments/",
            {
                "bill_id": str(bill.id),
                "amount": "1180.00",
                "payment_account_id": str(bank.id),
                "mode": "bank_transfer",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient balance", str(res.data))

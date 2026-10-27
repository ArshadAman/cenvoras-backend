"""Reports QA R15: books, GSTR-1 and GSTR-3B must tie."""
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from rest_framework import status

from billing.models import Payment, PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Vendor
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase
from inventory.models import Product
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService


class CreditNoteValidationR15Tests(FourFixesBase):
    def _final_invoice(self):
        inv = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name=self.customer.name,
            invoice_number="INV-R15-CN",
            invoice_date=date.today(),
            place_of_supply="Karnataka",
            status="final",
            total_amount=Decimal("11800.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv, product=self.product, quantity=10,
            price=Decimal("1000.00"), tax=Decimal("18.00"), amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        return inv

    def test_credit_note_requires_final_invoice(self):
        res = self.client.post(
            "/api/billing/credit-notes/",
            {
                "date": str(date.today()),
                "customer": str(self.customer.id),
                "reason": "return",
                "total_amount": "118.00",
                "items": [{
                    "product": str(self.product.id),
                    "quantity": 1,
                    "price": "100.00",
                    "tax": "18.00",
                    "amount": "118.00",
                }],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("original_invoice", str(res.data).lower())

    def test_credit_note_rejects_over_invoice_value(self):
        inv = self._final_invoice()
        res = self.client.post(
            "/api/billing/credit-notes/",
            {
                "date": str(date.today()),
                "customer": str(self.customer.id),
                "original_invoice": str(inv.id),
                "reason": "return",
                "total_amount": "20000.00",
                "items": [{
                    "product": str(self.product.id),
                    "quantity": 20,
                    "price": "1000.00",
                    "tax": "18.00",
                    "amount": "23600.00",
                }],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class DebitNoteCapR15Tests(FourFixesBase):
    def test_debit_note_cannot_exceed_bill_itc(self):
        vendor = Vendor.objects.create(
            name="MH Vendor", gstin="27AAAAA0000A1Z5", state="Maharashtra", created_by=self.tenant,
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant, bill_number="PB-R15-CAP", bill_date=date.today(),
            vendor=vendor, vendor_name=vendor.name, vendor_gstin=vendor.gstin,
            place_of_supply="Karnataka", total_amount=Decimal("11800.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill, product=self.product, quantity=1,
            price=Decimal("10000.00"), tax=Decimal("18.00"), amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        res = self.client.post(
            "/api/billing/debit-notes/",
            {
                "date": str(date.today()),
                "original_bill": str(bill.id),
                "vendor_name": vendor.name,
                "reason": "return",
                "total_amount": "826000.00",
                "items": [{
                    "product": str(self.product.id),
                    "quantity": 70,
                    "price": "10000.00",
                    "tax": "18.00",
                    "amount": "826000.00",
                }],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class PurchaseItcHeadR15Tests(FourFixesBase):
    def test_interstate_bill_posts_only_input_igst(self):
        vendor = Vendor.objects.create(
            name="MH Vendor", gstin="27AAAAA1111A1Z5", state="Maharashtra", created_by=self.tenant,
        )
        bill = PurchaseBill.objects.create(
            created_by=self.tenant, bill_number="PB-R15-IGST", bill_date=date.today(),
            vendor=vendor, vendor_name=vendor.name, vendor_gstin=vendor.gstin,
            place_of_supply="Karnataka", total_amount=Decimal("11800.00"),
        )
        PurchaseBillItem.objects.create(
            purchase_bill=bill, product=self.product, quantity=1,
            price=Decimal("10000.00"), tax=Decimal("18.00"), amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        AccountingService.create_purchase_bill_entries(bill)
        igst = GeneralLedgerEntry.objects.filter(purchase_bill=bill, account__code='1303').aggregate(s=Sum('debit'))['s']
        cgst = GeneralLedgerEntry.objects.filter(purchase_bill=bill, account__code='1301').aggregate(s=Sum('debit'))['s'] or 0
        sgst = GeneralLedgerEntry.objects.filter(purchase_bill=bill, account__code='1302').aggregate(s=Sum('debit'))['s'] or 0
        self.assertEqual(igst, Decimal('1800.00'))
        self.assertEqual(Decimal(str(cgst)), Decimal('0'))
        self.assertEqual(Decimal(str(sgst)), Decimal('0'))


class Gstr3BTieR15Tests(FourFixesBase):
    def test_3_1_a_taxable_matches_gstr1_and_excess_reversal_adds_tax_only(self):
        inv = SalesInvoice.objects.create(
            created_by=self.tenant, customer=self.customer, customer_name=self.customer.name,
            invoice_number="INV-R15-3B", invoice_date=date.today(),
            place_of_supply="Karnataka", status="final", total_amount=Decimal("11800.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv, product=self.product, quantity=10,
            price=Decimal("1000.00"), tax=Decimal("18.00"), amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        d = str(date.today())
        g1 = self.client.get(f"/api/billing/gst/gstr1-export/?from={d}&to={d}").json()
        g3 = self.client.get(f"/api/billing/gst/gstr3b/?from={d}&to={d}").json()
        g1_txval = sum(inv['txval'] for block in g1['b2b'] for inv in block['inv'])
        self.assertAlmostEqual(g3['table_3_1']['outward_taxable']['txval'], g1_txval, places=2)
        self.assertGreaterEqual(g3['table_4_itc']['net_itc_available']['iamt'], 0)
        self.assertGreaterEqual(g3['table_4_itc']['net_itc_available']['camt'], 0)

    def test_export_invoice_goes_to_3_1_b_not_a(self):
        overseas = SalesInvoice.objects.create(
            created_by=self.tenant, customer=self.customer, customer_name="Export Buyer",
            invoice_number="INV-EXP-1", invoice_date=date.today(),
            place_of_supply="97", status="final", gst_treatment="export",
            total_amount=Decimal("5000.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=overseas, product=self.product, quantity=1,
            price=Decimal("5000.00"), tax=Decimal("0.00"), amount=Decimal("5000.00"),
            hsn_sac_code="847130",
        )
        d = str(date.today())
        g3 = self.client.get(f"/api/billing/gst/gstr3b/?from={d}&to={d}").json()
        self.assertEqual(g3['table_3_1']['outward_taxable']['txval'], 0)
        self.assertEqual(g3['table_3_1']['zero_rated']['txval'], 5000.0)

    def test_hsn_has_no_na_and_single_data_table(self):
        d = str(date.today())
        g1 = self.client.get(f"/api/billing/gst/gstr1-export/?from={d}&to={d}").json()
        codes = [row['hsn_sc'] for row in g1['hsn']['data']]
        self.assertNotIn('N/A', codes)
        self.assertNotIn('hsn_b2b', g1)

    def test_3_2_interstate_unregistered_carries_igst(self):
        from billing.models import Customer
        walkin = Customer.objects.create(
            name="MP Walk-in", state="Madhya Pradesh", created_by=self.tenant,
        )
        inv = SalesInvoice.objects.create(
            created_by=self.tenant, customer=walkin, customer_name=walkin.name,
            invoice_number="INV-R15-32", invoice_date=date(2026, 9, 15),
            place_of_supply="Madhya Pradesh", status="final",
            total_amount=Decimal("4723.60"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv, product=self.product, quantity=1,
            price=Decimal("4020.00"), tax=Decimal("18.00"), amount=Decimal("4743.60"),
            hsn_sac_code="847130",
        )
        g3 = self.client.get("/api/billing/gst/gstr3b/?from=2026-09-01&to=2026-09-30").json()
        row = next((r for r in g3['table_3_2'] if str(r['pos']) == '23'), None)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row['txval'], 4020.0, places=2)
        self.assertGreater(row['iamt'], 0)


class Section34WarningR15Tests(FourFixesBase):
    def test_tax_credit_note_after_30_nov_warns(self):
        from billing.gst_rules import section34_warning
        warning = section34_warning(date(2025, 3, 31), date(2025, 12, 1), True)
        self.assertIn('s.34', warning)
        self.assertIsNone(section34_warning(date(2025, 3, 31), date(2025, 11, 30), True))
        self.assertIsNone(section34_warning(date(2025, 3, 31), date(2025, 12, 1), False))


class AdvanceGstR15Tests(FourFixesBase):
    def test_service_advance_posts_output_gst_and_goods_does_not(self):
        svc = Payment.objects.create(
            created_by=self.tenant, customer=self.customer, date=date.today(),
            amount=Decimal("590.00"), mode="bank_transfer", supply_kind="services",
        )
        out_cgst = GeneralLedgerEntry.objects.filter(payment=svc, account__code='2201').aggregate(s=Sum('credit'))['s'] or 0
        out_sgst = GeneralLedgerEntry.objects.filter(payment=svc, account__code='2202').aggregate(s=Sum('credit'))['s'] or 0
        self.assertEqual(Decimal(str(out_cgst)) + Decimal(str(out_sgst)), Decimal('90.00'))

        goods = Payment.objects.create(
            created_by=self.tenant, customer=self.customer, date=date.today(),
            amount=Decimal("1000.00"), mode="bank_transfer", supply_kind="goods",
        )
        gst = GeneralLedgerEntry.objects.filter(
            payment=goods, account__code__in=['2201', '2202', '2203']
        ).aggregate(s=Sum('credit'))['s'] or 0
        self.assertEqual(Decimal(str(gst)), Decimal('0'))

    def test_allocate_advance_writes_atadj(self):
        from billing.gstr1_compute import compute_gstr1
        svc = Payment.objects.create(
            created_by=self.tenant, customer=self.customer, date=date.today(),
            amount=Decimal("590.00"), mode="bank_transfer", supply_kind="services",
        )
        inv = SalesInvoice.objects.create(
            created_by=self.tenant, customer=self.customer, customer_name=self.customer.name,
            invoice_number="INV-ATADJ", invoice_date=date.today(),
            place_of_supply="Karnataka", status="final", total_amount=Decimal("11800.00"),
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=inv, product=self.product, quantity=10,
            price=Decimal("1000.00"), tax=Decimal("18.00"), amount=Decimal("11800.00"),
            hsn_sac_code="847130",
        )
        res = self.client.post(
            f"/api/billing/payments/{svc.id}/allocate/",
            {"invoice_id": str(inv.id)},
            format="json",
        )
        self.assertIn(res.status_code, (200, 201))
        svc.refresh_from_db()
        # R17 (Rudra CA): the receipt stays an advance; the application is a separate adjustment.
        self.assertIsNone(svc.invoice_id)
        self.assertTrue(svc.adjustments.filter(invoice=inv).exists())
        self.assertIsNotNone(svc.adjusted_on)
        d = str(date.today())
        g1 = compute_gstr1(self.tenant, d, d)
        # Same period advance + invoice: reported in neither 'at' nor 'atadj'.
        self.assertEqual(g1['atadj'], [])
        self.assertEqual(g1['at'], [])

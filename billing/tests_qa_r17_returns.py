"""QA R17: debit-note create from bill lines, remaining-qty endpoints."""
from datetime import date
from decimal import Decimal

from billing.models import PurchaseBill, PurchaseBillItem, Vendor
from billing.models_returns import DebitNote
from billing.tests_four_fixes import FourFixesBase


class DebitNoteR17Tests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.vendor = Vendor.objects.create(
            name="R17 MH Vendor", gstin="27AABCU9603R1ZM", state="Maharashtra", created_by=self.tenant)
        self.bill = PurchaseBill.objects.create(
            created_by=self.tenant, bill_number="R17-PB-0427", bill_date=date.today(),
            vendor=self.vendor, vendor_name=self.vendor.name, vendor_gstin=self.vendor.gstin,
            place_of_supply="Karnataka", total_amount=Decimal("5900.00"))
        PurchaseBillItem.objects.create(
            purchase_bill=self.bill, product=self.product, quantity=10,
            price=Decimal("500.00"), tax=Decimal("18.00"), amount=Decimal("5900.00"), hsn_sac_code="847130")

    def _dn(self, product_value, qty):
        return self.client.post("/api/billing/debit-notes/", {
            "date": str(date.today()), "original_bill": str(self.bill.id), "vendor_name": self.vendor.name,
            "reason": "return", "total_amount": str(Decimal("590.00") * qty),
            "items": [{"product": product_value, "quantity": qty, "price": "500.00", "tax": "18.00",
                       "amount": str(Decimal("590.00") * qty)}],
        }, format="json")

    def test_bill_line_exposes_product_uuid_in_product_detail(self):
        res = self.client.get(f"/api/billing/purchase-bills/{self.bill.id}/")
        self.assertEqual(res.status_code, 200)
        body = res.data.get("data", res.data)
        line = body["items"][0]
        self.assertNotEqual(line["product"], str(self.product.id))  # `product` is the name; FE must use product_detail.id
        self.assertEqual(line["product_detail"]["id"], str(self.product.id))

    def test_name_is_rejected_uuid_creates_with_decimal_total(self):
        self.assertEqual(self._dn(self.product.name, 2).status_code, 400)
        res = self._dn(str(self.product.id), 2)
        self.assertEqual(res.status_code, 201, res.data)
        dn = DebitNote.objects.get(pk=res.data["id"])
        self.assertEqual(dn.total_amount, Decimal("1180.00"))
        self.assertEqual(res.data["original_bill_number"], "R17-PB-0427")
        detail = self.client.get(f"/api/billing/debit-notes/{dn.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["original_bill_number"], "R17-PB-0427")

    def test_remaining_qty_endpoint_and_cap(self):
        self.assertEqual(self._dn(str(self.product.id), 2).status_code, 201)
        res = self.client.get("/api/billing/debit-notes/remaining/", {"bill": str(self.bill.id)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["remaining_qty"][str(self.product.id)], "8.00")
        over = self._dn(str(self.product.id), 9)
        self.assertEqual(over.status_code, 400)
        self.assertIn("8", str(over.data))
        self.assertEqual(self.client.get("/api/billing/debit-notes/remaining/", {"bill": "nope"}).status_code, 404)

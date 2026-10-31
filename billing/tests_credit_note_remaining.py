"""Credit note remaining qty API and over-credit rejection on create."""
from decimal import Decimal
from datetime import date

from rest_framework import status

from billing.models import SalesInvoice, SalesInvoiceItem
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase


class CreditNoteRemainingTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.invoice = SalesInvoice.objects.create(
            created_by=self.tenant,
            customer=self.customer,
            customer_name=self.customer.name,
            invoice_number="INV-CN-REM-001",
            invoice_date=date.today(),
            total_amount=Decimal("7080.00"),
            status="final",
        )
        SalesInvoiceItem.objects.create(
            sales_invoice=self.invoice,
            product=self.product,
            quantity=6,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("7080.00"),
            hsn_sac_code="847130",
        )

    def _cn_payload(self, qty):
        return {
            "date": str(date.today()),
            "customer": str(self.customer.id),
            "original_invoice": str(self.invoice.id),
            "reason": "return",
            "total_amount": "10.00",
            "items": [{
                "product": str(self.product.id),
                "quantity": qty,
                "price": "1000.00",
                "tax": "18.00",
                "amount": "1180.00",
                "hsn_sac_code": "847130",
            }],
        }

    def test_remaining_after_partial_credit_note(self):
        cn = CreditNote.objects.create(
            credit_note_number="CN-PARTIAL",
            date=date.today(),
            customer=self.customer,
            original_invoice=self.invoice,
            reason="return",
            total_amount=Decimal("5310.00"),
            created_by=self.tenant,
        )
        CreditNoteItem.objects.create(
            credit_note=cn,
            product=self.product,
            quantity=4,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("4720.00"),
            hsn_sac_code="847130",
        )

        res = self.client.get(
            "/api/billing/credit-notes/remaining/",
            {"invoice": str(self.invoice.id)},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        remaining = res.data["remaining_qty"][str(self.product.id)]
        self.assertEqual(Decimal(remaining), Decimal("2.00"))

    def test_create_above_remaining_returns_400(self):
        CreditNote.objects.create(
            credit_note_number="CN-USED",
            date=date.today(),
            customer=self.customer,
            original_invoice=self.invoice,
            reason="return",
            total_amount=Decimal("4720.00"),
            created_by=self.tenant,
        )
        CreditNoteItem.objects.create(
            credit_note=CreditNote.objects.get(credit_note_number="CN-USED"),
            product=self.product,
            quantity=4,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("4720.00"),
        )

        over = self._cn_payload(3)
        over["items"][0]["tax"] = "0.00"
        over["items"][0]["amount"] = "3000.00"
        res = self.client.post(
            "/api/billing/credit-notes/",
            over,
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertIn("remaining", str(res.data).lower())

    def test_create_within_remaining_returns_201(self):
        cn = CreditNote.objects.create(
            credit_note_number="CN-USED-OK",
            date=date.today(),
            customer=self.customer,
            original_invoice=self.invoice,
            reason="return",
            total_amount=Decimal("4720.00"),
            created_by=self.tenant,
        )
        CreditNoteItem.objects.create(
            credit_note=cn,
            product=self.product,
            quantity=4,
            price=Decimal("1000.00"),
            tax=Decimal("18.00"),
            amount=Decimal("4720.00"),
        )

        res = self.client.post(
            "/api/billing/credit-notes/",
            self._cn_payload(2),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        remaining = self.client.get(
            "/api/billing/credit-notes/remaining/",
            {"invoice": str(self.invoice.id)},
        ).data["remaining_qty"][str(self.product.id)]
        self.assertEqual(Decimal(remaining), Decimal("0.00"))

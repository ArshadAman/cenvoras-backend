from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from billing.models import Customer, SalesInvoice
from inventory.models import Product
from subscription.models import Plan, TenantSubscription

User = get_user_model()


class SalesItemOrderTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = User.objects.create_user(
            username="item_order_tenant",
            email="item_order@test.com",
            password="testpassword",
            first_name="Item",
            last_name="Order",
        )
        plan, _ = Plan.objects.get_or_create(code="pro", defaults={"name": "Pro", "monthly_price": 0})
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan=plan,
            status="active",
            current_period_end=date.today() + timedelta(days=365),
        )
        self.client.force_authenticate(user=self.tenant)
        self.customer = Customer.objects.create(name="Order Customer", created_by=self.tenant)
        self.product_a = Product.objects.create(
            name="Alpha Widget",
            hsn_sac_code="847130",
            sale_price=Decimal("100.00"),
            price=Decimal("100.00"),
            tax=Decimal("18.00"),
            unit="pcs",
            stock=50,
            created_by=self.tenant,
        )
        self.product_b = Product.objects.create(
            name="Beta Widget",
            hsn_sac_code="847130",
            sale_price=Decimal("50.00"),
            price=Decimal("50.00"),
            tax=Decimal("18.00"),
            unit="pcs",
            stock=50,
            created_by=self.tenant,
        )

    def _product_line(self, product, quantity=1, item_id=None):
        line = {
            "row_type": "item",
            "product": str(product.id),
            "quantity": quantity,
            "price": str(product.sale_price),
            "hsn_sac_code": product.hsn_sac_code,
        }
        if item_id:
            line["id"] = str(item_id)
        return line

    def _note_line(self, text, item_id=None):
        line = {
            "row_type": "note",
            "product": None,
            "description": text,
            "quantity": 0,
            "price": "0.00",
        }
        if item_id:
            line["id"] = str(item_id)
        return line

    def _product_id(self, item):
        detail = item.get("product_detail") or {}
        if detail.get("id"):
            return str(detail["id"])
        return str(item.get("product") or "")

    def _row_sig(self, items):
        sig = []
        for item in items:
            row_type = item.get("row_type") or "item"
            if row_type == "note":
                sig.append(("note", (item.get("description") or "").strip()))
            else:
                sig.append(("item", self._product_id(item)))
        return sig

    def test_confirm_keeps_start_and_mid_notes_without_note_ids(self):
        create = self.client.post("/api/billing/sales-invoices/", {
            "customer_name": self.customer.name,
            "invoice_date": str(date.today()),
            "status": "draft",
            "items": [
                self._note_line("Intro note"),
                self._product_line(self.product_a),
                self._note_line("Mid note"),
                self._product_line(self.product_b),
            ],
        }, format="json")
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, getattr(create, "data", create.content))
        invoice_id = create.data["id"]
        expected = [
            ("note", "Intro note"),
            ("item", str(self.product_a.id)),
            ("note", "Mid note"),
            ("item", str(self.product_b.id)),
        ]
        self.assertEqual(self._row_sig(create.data["items"]), expected)

        confirm = self.client.put(f"/api/billing/sales-invoices/{invoice_id}/edit/", {
            "customer_name": self.customer.name,
            "invoice_number": create.data["invoice_number"],
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                self._note_line("Intro note"),
                self._product_line(self.product_a),
                self._note_line("Mid note"),
                self._product_line(self.product_b),
            ],
        }, format="json")
        self.assertEqual(confirm.status_code, status.HTTP_200_OK, getattr(confirm, "data", confirm.content))
        self.assertEqual(self._row_sig(confirm.data["items"]), expected)

        invoice = SalesInvoice.objects.get(id=invoice_id)
        db_sig = [
            ("note", item.description.strip()) if item.row_type == "note" else ("item", str(item.product_id))
            for item in invoice.items.all()
        ]
        self.assertEqual(db_sig, expected)

    def test_insert_note_at_top_stays_first(self):
        create = self.client.post("/api/billing/sales-invoices/", {
            "customer_name": self.customer.name,
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                self._product_line(self.product_a),
                self._product_line(self.product_b),
            ],
        }, format="json")
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, getattr(create, "data", create.content))
        invoice_id = create.data["id"]
        existing = {self._product_id(item): item["id"] for item in create.data["items"]}

        update = self.client.put(f"/api/billing/sales-invoices/{invoice_id}/edit/", {
            "customer_name": self.customer.name,
            "invoice_number": create.data["invoice_number"],
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                self._note_line("Top note"),
                self._product_line(self.product_a, item_id=existing[str(self.product_a.id)]),
                self._product_line(self.product_b, item_id=existing[str(self.product_b.id)]),
            ],
        }, format="json")
        self.assertEqual(update.status_code, status.HTTP_200_OK, getattr(update, "data", update.content))
        self.assertEqual(self._row_sig(update.data["items"]), [
            ("note", "Top note"),
            ("item", str(self.product_a.id)),
            ("item", str(self.product_b.id)),
        ])

    def test_reorder_persists_on_get(self):
        create = self.client.post("/api/billing/sales-invoices/", {
            "customer_name": self.customer.name,
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                self._product_line(self.product_a),
                self._note_line("Between"),
                self._product_line(self.product_b),
            ],
        }, format="json")
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, getattr(create, "data", create.content))
        invoice_id = create.data["id"]
        created_items = create.data["items"]
        a_id = created_items[0]["id"]
        note_id = created_items[1]["id"]
        b_id = created_items[2]["id"]

        update = self.client.put(f"/api/billing/sales-invoices/{invoice_id}/edit/", {
            "customer_name": self.customer.name,
            "invoice_number": create.data["invoice_number"],
            "invoice_date": str(date.today()),
            "status": "final",
            "items": [
                self._product_line(self.product_b, item_id=b_id),
                self._product_line(self.product_a, item_id=a_id),
                self._note_line("Between", item_id=note_id),
            ],
        }, format="json")
        self.assertEqual(update.status_code, status.HTTP_200_OK, getattr(update, "data", update.content))
        expected = [
            ("item", str(self.product_b.id)),
            ("item", str(self.product_a.id)),
            ("note", "Between"),
        ]
        self.assertEqual(self._row_sig(update.data["items"]), expected)

        refetch = self.client.get(f"/api/billing/sales-invoices/{invoice_id}/")
        self.assertEqual(refetch.status_code, status.HTTP_200_OK)
        self.assertEqual(self._row_sig(refetch.data["items"]), expected)

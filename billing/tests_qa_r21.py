"""R21 QA fixes: receipt with a new customer (atomic, idempotent), typed-product errors, GSTR-1 carry
labels and zero b2cs rows, CN date + decimal quantity."""
from datetime import date, timedelta
from decimal import Decimal

from billing.models import Customer, Payment, SalesInvoice, SalesInvoiceItem, GSTR1Filing
from billing.models_returns import CreditNote
from billing.tests_four_fixes import FourFixesBase


class R21ReceiptNewCustomerTests(FourFixesBase):
    def _post(self, **extra):
        payload = {"new_customer_name": "R21 KA Cust", "date": str(date.today()), "amount": "1000",
                   "mode": "cash", "auto_allocate": False}
        payload.update(extra)
        return self.client.post("/api/billing/payments/", payload, format="json")

    def test_failed_payment_creates_no_customer(self):
        res = self._post()  # advance not classified -> rejected
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(Customer.objects.filter(name="R21 KA Cust").exists())
        self.assertEqual(Payment.objects.count(), 0)

    def test_success_creates_customer_once_and_retry_reuses_it(self):
        ok = dict(supply_kind="goods")
        res = self._post(**ok)
        self.assertEqual(res.status_code, 201, res.data)
        cust = Customer.objects.get(name="R21 KA Cust")
        self.assertEqual(str(Payment.objects.get().customer_id), str(cust.id))
        self.assertEqual(self._post(**ok).status_code, 201)
        self._post()  # a failing retry
        self.assertEqual(Customer.objects.filter(name__iexact="r21 ka cust").count(), 1)
        self.assertEqual(Payment.objects.filter(customer=cust).count(), 2)

    def test_no_customer_at_all_is_a_clear_400(self):
        res = self._post(new_customer_name="  ", supply_kind="goods")
        self.assertEqual(res.status_code, 400)
        self.assertIn("customer", res.data)


class R21TypedProductTests(FourFixesBase):
    def _inv(self, confirm):
        self.customer.allow_credit = True
        self.customer.save()
        item = {"row_type": "item", "product": "Typed Widget X", "product_name": "Typed Widget X", "quantity": 1,
                "price": "100.00", "tax": "18.00", "amount": "100.00", "hsn_sac_code": "847130", "unit": "pcs"}
        if confirm:
            item["confirm_create_product"] = True
        return self.client.post("/api/billing/sales-invoices/", {
            "customer_name": self.customer.name, "invoice_date": str(date.today()), "status": "final",
            "total_amount": "118.00", "items": [item]}, format="json")

    def test_typed_product_needs_confirm_and_says_so(self):
        res = self._inv(confirm=False)
        self.assertEqual(res.status_code, 400)
        self.assertIn("confirm_create_product", str(res.data))   # message the UI now shows / pre-empts
        self.assertEqual(self._inv(confirm=True).status_code, 201)

    def test_save_as_new_product_requires_hsn_with_message(self):
        body = {"name": "Typed Thing", "sale_price": 100, "cost_price": 100, "unit": "pcs", "tax": 18, "stock": 0}
        res = self.client.post("/api/inventory/products/", {**body, "hsn_sac_code": None}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("HSN/SAC code is required", str(res.data))
        res = self.client.post("/api/inventory/products/", {**body, "hsn_sac_code": "847130"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)


class R21Gstr1LabelTests(FourFixesBase):
    def test_row_label(self):
        from billing.gstr1_compute import row_label
        self.assertEqual(row_label('b2cs', ('32', 5.0, 'INTER')), 'B2CS Kerala (32), 5%, inter-state')
        self.assertEqual(row_label('table8', 'INTRAB2C'), 'Table 8 intra-state, unregistered')
        self.assertEqual(row_label('table8', 'INTRB2B'), 'Table 8 inter-state, registered')

    def test_zero_b2cs_rows_not_in_json_but_on_screen(self):
        from billing.tests_gstr1_b2cl_cdnur_pos import _G1Helpers  # noqa: F401
        kl = Customer.objects.create(name="KL Walkin", state="Kerala", created_by=self.tenant)
        aug = SalesInvoice.objects.create(customer=kl, customer_name=kl.name, invoice_number="A1",
                                          invoice_date=date(2026, 8, 5), total_amount=Decimal("10500"),
                                          created_by=self.tenant)
        SalesInvoiceItem.objects.create(sales_invoice=aug, product=self.product, hsn_sac_code="847130",
                                        quantity=1, price=Decimal("10000"), tax=Decimal("5"), amount=Decimal("10500"))
        sep = SalesInvoice.objects.create(customer=kl, customer_name=kl.name, invoice_number="S1",
                                          invoice_date=date(2026, 9, 5), total_amount=Decimal("1050"),
                                          created_by=self.tenant)
        SalesInvoiceItem.objects.create(sales_invoice=sep, product=self.product, hsn_sac_code="847130",
                                        quantity=1, price=Decimal("1000"), tax=Decimal("5"), amount=Decimal("1050"))
        cn = CreditNote.objects.create(credit_note_number="CN-9", date=date(2026, 9, 20), original_invoice=aug,
                                       customer=kl, created_by=self.tenant, total_amount=Decimal("6300"))
        from billing.models_returns import CreditNoteItem
        CreditNoteItem.objects.create(credit_note=cn, product=self.product, hsn_sac_code="847130", quantity=1,
                                      price=Decimal("6000"), tax=Decimal("5"), amount=Decimal("6300"))
        res = self.client.get("/api/billing/gst/gstr1-export/?from=2026-09-01&to=2026-09-30")
        self.assertEqual(res.data['b2cs'], [])
        meta = res.data['_cenvora']
        self.assertEqual(meta['b2cs_zero_rows'][0]['label'], 'B2CS Kerala (32), 5%, inter-state')
        self.assertTrue(any('Kerala (32), 5%, inter-state' in w['message'] for w in meta['warnings']))
        self.assertFalse(any("('32'" in w['message'] for w in meta['warnings']))


class R21CreditNoteDateQtyTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.inv = SalesInvoice.objects.create(customer=self.customer, customer_name="KA Buyer", invoice_number="I-1",
                                               invoice_date=date.today() - timedelta(days=3),
                                               total_amount=Decimal("2360"), created_by=self.tenant)
        SalesInvoiceItem.objects.create(sales_invoice=self.inv, product=self.product, hsn_sac_code="847130",
                                        quantity=2, price=Decimal("1000"), tax=Decimal("18"), amount=Decimal("2360"))

    def _post(self, qty=1, d=None, reason="return"):
        payload = {"original_invoice": str(self.inv.id), "customer": str(self.customer.id), "reason": reason,
                   "total_amount": "0",
                   "items": [{"product": str(self.product.id), "quantity": qty, "price": "1000.00",
                              "tax": "18.00", "amount": "0"}]}
        if d is not None:
            payload["date"] = str(d)
        return self.client.post("/api/billing/credit-notes/", payload, format="json")

    def test_date_defaults_today(self):
        res = self._post()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['date'], str(date.today()))

    def test_date_before_invoice_or_future_rejected(self):
        self.assertIn('before the invoice date', str(self._post(d=self.inv.invoice_date - timedelta(days=1)).data))
        self.assertIn('future', str(self._post(d=date.today() + timedelta(days=1)).data))

    def test_date_in_filed_month_rejected(self):
        GSTR1Filing.objects.create(created_by=self.tenant, period=date.today().replace(day=1), filed_on=date.today())
        res = self._post()
        self.assertEqual(res.status_code, 400)
        self.assertIn("GSTR-1 is filed", str(res.data))

    def test_decimal_quantity_for_discount_but_whole_units_for_returns(self):
        self.product.refresh_from_db()
        stock0 = self.product.stock
        self.assertIn('whole units', str(self._post(qty="0.5").data))
        res = self._post(qty="0.5", reason="discount")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Decimal(str(res.data['items'][0]['quantity'])), Decimal("0.50"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock0)  # part-quantity credit does not move stock
        self.assertIn('2 decimal', str(self._post(qty="0.555", reason="discount").data))


class R21CnStockAndBackdateTests(R21CreditNoteDateQtyTests):
    """Rudra CA on #192: only Goods Returned / Defective move stock; back-dated flag; audit fields."""

    def _stock(self):
        self.product.refresh_from_db()
        return self.product.stock

    def _no_stock(self, reason):
        if True:
            s0 = self._stock()
            res = self._post(qty=1, reason=reason)
            self.assertEqual(res.status_code, 201, res.data)
            self.assertEqual(self._stock(), s0, reason)
            self.assertIs(CreditNote.objects.get(pk=res.data['id']).stock_restored, False)

    def test_discount_never_touches_stock(self):
        self._no_stock('discount')

    def test_rate_diff_never_touches_stock(self):
        self._no_stock('rate_diff')

    def test_other_never_touches_stock(self):
        self._no_stock('other')

    def test_returned_and_defective_restore_stock(self):
        s0 = self._stock()
        res = self._post(qty=1, reason='return')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._stock(), s0 + 1)
        res = self._post(qty=1, reason='defective')
        self.assertEqual(self._stock(), s0 + 2)
        self.assertIs(CreditNote.objects.get(pk=res.data['id']).stock_restored, True)

    def test_back_dated_flag_and_audit_fields(self):
        res = self._post(qty=1, reason='discount', d=date.today() - timedelta(days=1))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn('Back-dated entry', res.data['back_dated']['message'])
        cn = CreditNote.objects.get(pk=res.data['id'])
        self.assertEqual(cn.entered_by_id, self.tenant.id)
        self.assertNotEqual(cn.created_at.date(), cn.date)
        self.assertTrue(res.data['entered_by_name'])
        res = self._post(qty=1, reason='discount')
        self.assertIsNone(res.data['back_dated'])


class ListCnStockRestoresCommandTests(R21CreditNoteDateQtyTests):
    def test_lists_only_value_cns_that_restored_stock_and_writes_nothing(self):
        from io import StringIO
        from django.core.management import call_command
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from billing.models_returns import CreditNoteItem

        def cn(num, reason, restored):
            c = CreditNote.objects.create(credit_note_number=num, date=date.today(), original_invoice=self.inv,
                                          customer=self.customer, reason=reason, total_amount=Decimal("1180"),
                                          created_by=self.tenant, stock_restored=restored)
            CreditNoteItem.objects.create(credit_note=c, product=self.product, quantity=1, price=Decimal("1000"),
                                          tax=Decimal("18"), amount=Decimal("1180"))
        cn("CN-LEG", "discount", None)      # pre-R21: inferred restore
        cn("CN-REC", "rate_diff", True)     # recorded restore
        cn("CN-OK", "discount", False)      # correct: no restore
        cn("CN-RET", "return", None)        # goods return: fine
        out = StringIO()
        with CaptureQueriesContext(connection) as ctx:
            call_command('list_cn_stock_restores', stdout=out)
        text = out.getvalue()
        self.assertIn('CN-LEG', text)
        self.assertIn('CN-REC', text)
        self.assertNotIn('CN-OK', text)
        self.assertNotIn('CN-RET', text)
        self.assertIn('inferred', text)
        self.assertIn('READ-ONLY', text)
        writes = [q['sql'] for q in ctx.captured_queries
                  if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(writes, [])

    def test_no_apply_flag(self):
        from django.core.management import call_command, CommandError
        with self.assertRaises((CommandError, SystemExit, TypeError)):
            call_command('list_cn_stock_restores', '--apply')

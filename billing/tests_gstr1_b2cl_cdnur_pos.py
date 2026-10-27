"""Rudra CA rules: B2CL > Rs 1,00,000 (Notfn 12/2024), CN netting into b2cs, cdnur only for
B2CL/export invoices, POS default + override rules. Tenant is Karnataka, so Odisha = inter-state."""
from datetime import date
from decimal import Decimal

from billing.gstr1_compute import compute_gstr1
from billing.gst_rules import invoice_pos_decision
from billing.models import Customer, SalesInvoice, SalesInvoiceItem
from billing.models_returns import CreditNote, CreditNoteItem
from billing.tests_four_fixes import FourFixesBase
from inventory.models import Product

D = date(2026, 9, 10)


class Gstr1B2clCdnurPosTests(FourFixesBase):
    def setUp(self):
        super().setUp()
        self.od = Customer.objects.create(name="OD Walkin", state="Odisha", created_by=self.tenant)
        self.ka = Customer.objects.create(name="KA Walkin", state="Karnataka", created_by=self.tenant)
        self.svc = Product.objects.create(name="Consulting", hsn_sac_code="998311", sale_price=1,
                                          tax=Decimal("18"), unit="nos", stock=0, created_by=self.tenant)
        self.n = 0

    def inv(self, cust, total, price=None, treatment=None, pos=None):
        self.n += 1
        inv = SalesInvoice.objects.create(customer=cust, customer_name=cust.name, invoice_number=f"INV-{self.n}",
                                          invoice_date=D, total_amount=Decimal(total), created_by=self.tenant,
                                          gst_treatment=treatment, place_of_supply=pos)
        p = Decimal(price) if price else (Decimal(total) / Decimal("1.18")).quantize(Decimal("0.01"))
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, hsn_sac_code="847130",
                                        quantity=1, price=p, tax=Decimal("0" if treatment else "18"), amount=total)
        SalesInvoice.objects.filter(pk=inv.pk).update(total_amount=Decimal(total))  # pin invoice value
        inv.refresh_from_db()
        return inv

    def cn(self, inv, price, tax="18"):
        self.n += 1
        c = CreditNote.objects.create(credit_note_number=f"CN-{self.n}", date=D, original_invoice=inv,
                                      customer=inv.customer, created_by=self.tenant,
                                      total_amount=Decimal(price))
        CreditNoteItem.objects.create(credit_note=c, product=self.product, hsn_sac_code="847130", quantity=1,
                                      price=Decimal(price), tax=Decimal(tax), amount=Decimal(price))
        return c

    def run_(self):
        return compute_gstr1(self.tenant, date(2026, 9, 1), date(2026, 9, 30), apply_carry=False)

    def test_exactly_one_lakh_is_b2cs_and_one_lakh_one_is_b2cl(self):
        self.inv(self.od, "100000.00")
        self.inv(self.od, "100001.00")
        r = self.run_()
        self.assertEqual([i['inv'][0]['val'] for i in r['b2cl']], [100001.0])
        self.assertEqual(r['b2cl'][0]['pos'], '21')
        self.assertEqual(len(r['b2cs']), 1)
        self.assertEqual(r['b2cs'][0]['sply_ty'], 'INTER')

    def test_intra_state_above_threshold_stays_b2cs(self):
        self.inv(self.ka, "300000.00")
        r = self.run_()
        self.assertEqual(r['b2cl'], [])
        self.assertEqual(r['b2cs'][0]['sply_ty'], 'INTRA')
        self.assertEqual(r['b2cs'][0]['pos'], '29')

    def test_cn_on_b2cs_invoice_nets_into_b2cs_not_cdnur(self):
        self.inv(self.od, "11800.00", price="10000.00")
        self.cn(SalesInvoice.objects.get(invoice_number="INV-1"), "1000.00")
        r = self.run_()
        self.assertEqual(r['cdnur'], [])
        self.assertEqual(len(r['b2cs']), 1)
        self.assertAlmostEqual(r['b2cs'][0]['txval'], 9000.0)
        self.assertAlmostEqual(r['b2cs'][0]['iamt'], 1620.0)

    def test_cn_on_b2cl_invoice_goes_to_cdnur_b2cl_even_if_small(self):
        inv = self.inv(self.od, "200000.00")
        self.cn(inv, "1000.00")
        r = self.run_()
        self.assertEqual([c['typ'] for c in r['cdnur']], ['B2CL'])
        self.assertAlmostEqual(r['b2cs'][0]['txval'] if r['b2cs'] else 0.0, 0.0)

    def test_cn_on_export_invoice_goes_to_cdnur_exp(self):
        inv = self.inv(self.od, "50000.00", price="50000.00", treatment="lut")
        self.cn(inv, "1000.00", tax="0")
        r = self.run_()
        self.assertEqual([c['typ'] for c in r['cdnur']], ['EXPWOP'])
        self.assertEqual(r['b2cs'], [])

    def test_pos_defaults_gstin_state_over_address_state(self):
        c = Customer.objects.create(name="Mixed", gstin="21ABCDE1234F1Z5", state="Karnataka", created_by=self.tenant)
        self.assertEqual(invoice_pos_decision(c, None, [])[0], '21')
        self.assertEqual(invoice_pos_decision(self.od, None, [])[0], '21')
        inv = self.inv(Customer.objects.create(name="Mixed2", gstin="", state="Odisha", created_by=self.tenant), "1000.00")
        self.assertEqual(self.run_()['b2cs'][0]['pos'], '21')
        self.assertTrue(inv)

    def test_goods_override_allowed_with_warning(self):
        code, warn, err = invoice_pos_decision(self.ka, "Odisha", [{"hsn_sac_code": "847130"}])
        self.assertEqual((code, err), ('21', None))
        self.assertIn("differs", warn)

    def test_services_and_bill_to_ship_to_override_blocked(self):
        _, _, err = invoice_pos_decision(self.ka, "Odisha", [{"hsn_sac_code": "998311"}])
        self.assertIn("s.12", err)
        _, _, err = invoice_pos_decision(self.ka, "Odisha", [{"hsn_sac_code": "847130"}], bill_to_ship_to=True)
        self.assertIn("s.10", err)
        self.assertEqual(invoice_pos_decision(self.ka, "Karnataka", [{"hsn_sac_code": "998311"}])[2], None)

    def _post(self, pos, product, hsn, extra=None):
        payload = {"customer_name": self.ka.name, "invoice_date": str(date.today()), "status": "final",
                   "total_amount": "118.00", "place_of_supply": pos,
                   "items": [{"product": product, "quantity": 1, "price": "100.00", "tax": "18.00",
                              "amount": "118.00", "hsn_sac_code": hsn}]}
        payload.update(extra or {})
        return self.client.post("/api/billing/sales-invoices/", payload, format="json")

    def test_api_blocks_services_mismatch_and_warns_goods(self):
        self.ka.allow_credit = True
        self.ka.save()
        res = self._post("OR", "Consulting", "998311")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("place_of_supply", str(res.data))
        res = self._post("OR", "Widget", "847130", {"bill_to_ship_to": True})
        self.assertEqual(res.status_code, 400, res.data)
        res = self._post("OR", "Widget", "847130")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data.get("pos_warning"))

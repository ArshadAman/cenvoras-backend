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


class _G1Helpers:
    def setUp(self):
        super().setUp()
        self.od = Customer.objects.create(name="OD Walkin", state="Odisha", created_by=self.tenant)
        self.ka = Customer.objects.create(name="KA Walkin", state="Karnataka", created_by=self.tenant)
        self.svc = Product.objects.create(name="Consulting", hsn_sac_code="998311", sale_price=1,
                                          tax=Decimal("18"), unit="nos", stock=0, created_by=self.tenant)
        self.n = 0

    def inv(self, cust, total, price=None, treatment=None, pos=None, d=D, tax=None):
        self.n += 1
        inv = SalesInvoice.objects.create(customer=cust, customer_name=cust.name, invoice_number=f"INV-{self.n}",
                                          invoice_date=d, total_amount=Decimal(total), created_by=self.tenant,
                                          gst_treatment=treatment, place_of_supply=pos)
        p = Decimal(price) if price else (Decimal(total) / Decimal("1.18")).quantize(Decimal("0.01"))
        SalesInvoiceItem.objects.create(sales_invoice=inv, product=self.product, hsn_sac_code="847130",
                                        quantity=1, price=p, tax=Decimal(tax if tax is not None else ("0" if treatment else "18")), amount=total)
        SalesInvoice.objects.filter(pk=inv.pk).update(total_amount=Decimal(total))  # pin invoice value
        inv.refresh_from_db()
        return inv

    def cn(self, inv, price, tax="18", d=D):
        self.n += 1
        c = CreditNote.objects.create(credit_note_number=f"CN-{self.n}", date=d, original_invoice=inv,
                                      customer=inv.customer, created_by=self.tenant,
                                      total_amount=Decimal(price))
        CreditNoteItem.objects.create(credit_note=c, product=self.product, hsn_sac_code="847130", quantity=1,
                                      price=Decimal(price), tax=Decimal(tax), amount=Decimal(price))
        return c

    def run_(self):
        return compute_gstr1(self.tenant, date(2026, 9, 1), date(2026, 9, 30), apply_carry=False)


class Gstr1B2clCdnurPosTests(_G1Helpers, FourFixesBase):

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


class Gstr1CnFloorCarryTests(_G1Helpers, FourFixesBase):
    """R21 additions (Rudra CA sign-off on #191)."""

    def g1(self, y, m):
        import calendar
        return compute_gstr1(self.tenant, date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]))

    def test_cn_on_nil_invoice_reduces_table8_same_bucket(self):
        inv = self.inv(self.ka, "10000.00", price="10000.00", tax="0")
        self.cn(inv, "1000.00", tax="0")
        r = self.run_()
        self.assertEqual(r['nil'], [{'sply_ty': 'INTRAB2C', 'nil_amt': 9000.0, 'expt_amt': 0.0, 'ngsup_amt': 0.0}])
        self.assertEqual(r['cdnur'], [])

    def test_cn_on_b2b_taxable_invoice_stays_in_cdnr(self):
        inv = self.inv(self.customer, "11800.00", price="10000.00")
        self.cn(inv, "1000.00")
        r = self.run_()
        self.assertEqual(len(r['cdnr']), 1)
        self.assertEqual(r['nil'], [])

    def test_b2cs_floor_and_carry_into_next_month(self):
        aug = self.inv(self.od, "11800.00", price="10000.00", d=date(2026, 8, 5))
        self.inv(self.od, "1180.00", price="1000.00", d=date(2026, 9, 5))
        self.cn(aug, "3000.00", d=date(2026, 9, 20))
        self.inv(self.od, "5900.00", price="5000.00", d=date(2026, 10, 5))
        sep = self.g1(2026, 9)
        row = sep['b2cs'][0]
        self.assertEqual((row['txval'], row['iamt']), (0.0, 0.0))
        self.assertTrue(any('2,000.00 of credit notes carried forward' in w['message'] for w in sep['cn_warnings']))
        oct_ = self.g1(2026, 10)
        self.assertEqual(oct_['b2cs'][0]['txval'], 3000.0)
        self.assertEqual(oct_['b2cs'][0]['iamt'], 540.0)
        self.assertEqual(oct_['cn_warnings'], [])

    def test_table8_floor_and_carry(self):
        aug = self.inv(self.ka, "5000.00", price="5000.00", tax="0", d=date(2026, 8, 5))
        self.inv(self.ka, "500.00", price="500.00", tax="0", d=date(2026, 9, 5))
        self.cn(aug, "2000.00", tax="0", d=date(2026, 9, 20))
        self.inv(self.ka, "4000.00", price="4000.00", tax="0", d=date(2026, 10, 5))
        self.assertEqual(self.g1(2026, 9)['nil'][0]['nil_amt'], 0.0)
        self.assertEqual(self.g1(2026, 10)['nil'][0]['nil_amt'], 2500.0)

    def test_carry_blocked_after_s34_deadline(self):
        # FY 2024-25 invoice -> s.34(2) deadline 30 Nov 2025.
        old = self.inv(self.od, "11800.00", price="10000.00", d=date(2024, 6, 1))
        self.inv(self.od, "1180.00", price="1000.00", d=date(2025, 11, 5))
        self.cn(old, "3000.00", d=date(2025, 11, 20))
        self.inv(self.od, "5900.00", price="5000.00", d=date(2025, 12, 5))
        dec = self.g1(2025, 12)
        self.assertEqual(dec['b2cs'][0]['txval'], 5000.0)
        self.assertTrue(any(w['code'] == 'cn_carry_expired' for w in dec['cn_warnings']))

    def test_cn_dated_after_s34_deadline_not_netted(self):
        old = self.inv(self.od, "11800.00", price="10000.00", d=date(2024, 6, 1))
        self.inv(self.od, "5900.00", price="5000.00", d=date(2025, 12, 5))
        self.cn(old, "1000.00", d=date(2025, 12, 10))
        dec = self.g1(2025, 12)
        self.assertEqual(dec['b2cs'][0]['txval'], 5000.0)
        self.assertTrue(any(w['code'] == 'cn_after_s34' for w in dec['cn_warnings']))

    def test_mixed_goods_services_warns_never_blocks(self):
        items = [{"hsn_sac_code": "847130"}, {"hsn_sac_code": "998311"}]
        code, warn, err = invoice_pos_decision(self.ka, "Odisha", items)
        self.assertEqual((code, err), ('21', None))
        self.assertIn('Mixed goods and services: confirm POS', warn)
        self.assertIn('Mixed goods and services', invoice_pos_decision(self.ka, None, items)[1])

    def test_print_pos_label_and_delivery_note(self):
        from billing.gst_utils import pos_display
        from billing.invoice_pdf_service import generate_invoice_pdf
        self.assertEqual(pos_display('OR'), 'Odisha (21)')
        self.assertEqual(pos_display('21'), 'Odisha (21)')
        inv = self.inv(self.od, "1180.00", pos='21')
        inv.delivery_address = 'Warehouse 4, Patna, Bihar'
        inv.save()
        self.assertTrue(generate_invoice_pdf(inv, self.tenant)[:4] == b'%PDF')

    def test_gstr1_export_api_has_nil_and_warnings(self):
        inv = self.inv(self.ka, "10000.00", price="10000.00", tax="0")
        self.cn(inv, "1000.00", tax="0")
        res = self.client.get("/api/billing/gst/gstr1-export/?from=2026-09-01&to=2026-09-30")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['_cenvora']['table8'][0]['nil_amt'], 9000.0)
        self.assertIn('warnings', res.data['_cenvora'])


class Gstr1S34RudraR22Tests(_G1Helpers, FourFixesBase):
    """Rudra CA answers on #191: taxed-CN-on-untaxed warning, s.34(2) declaration rule, time-barred CNs."""

    def g1(self, y, m):
        import calendar
        return compute_gstr1(self.tenant, date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]))

    def _sep_excess_fy2425(self):
        old = self.inv(self.od, "11800.00", price="10000.00", d=date(2024, 6, 1))   # deadline 30 Nov 2025
        self.inv(self.od, "1180.00", price="1000.00", d=date(2025, 9, 5))
        self.cn(old, "3000.00", d=date(2025, 9, 20))                                # 2,000 excess carried
        self.inv(self.od, "5900.00", price="5000.00", d=date(2025, 10, 5))

    def _file(self, y, m, on):
        from billing.models import GSTR1Filing
        GSTR1Filing.objects.create(created_by=self.tenant, period=date(y, m, 1), filed_on=on)

    def test_taxed_cn_on_untaxed_invoice_warns_and_keeps_old_routing(self):
        inv = self.inv(self.ka, "10000.00", price="10000.00", tax="0")
        self.cn(inv, "1000.00", tax="18")
        r = self.run_()
        self.assertTrue(any(w['code'] == 'cn_taxed_on_untaxed_invoice' for w in r['cn_warnings']))
        self.assertEqual(r['nil'][0]['nil_amt'], 10000.0)            # Table 8 not reduced
        self.assertEqual(r['b2cs'][0]['txval'], -1000.0)              # old routing (unfloored, no carry)

    def test_carry_usable_in_october_if_october_filed_by_30_nov(self):
        self._sep_excess_fy2425()
        self._file(2025, 10, date(2025, 11, 10))
        self.assertEqual(self.g1(2025, 10)['b2cs'][0]['txval'], 3000.0)

    def test_carry_dropped_if_october_filed_after_30_nov(self):
        self._sep_excess_fy2425()
        self._file(2025, 10, date(2025, 12, 5))
        r = self.g1(2025, 10)
        self.assertEqual(r['b2cs'][0]['txval'], 5000.0)
        self.assertTrue(any(w['code'] == 'cn_carry_expired' and 'after 30 Nov' in w['message']
                            for w in r['cn_warnings']))

    def test_carry_dropped_if_october_never_filed(self):
        self._sep_excess_fy2425()
        r = self.g1(2025, 10)
        self.assertEqual(r['b2cs'][0]['txval'], 5000.0)
        self.assertTrue(any('not filed by 2025-11-30' in w['message'] for w in r['cn_warnings']))

    def test_carry_not_usable_after_october_period(self):
        old = self.inv(self.od, "11800.00", price="10000.00", d=date(2024, 6, 1))
        self.inv(self.od, "1180.00", price="1000.00", d=date(2025, 10, 5))
        self.cn(old, "3000.00", d=date(2025, 10, 20))
        self._file(2025, 10, date(2025, 11, 10))
        self.inv(self.od, "5900.00", price="5000.00", d=date(2025, 11, 5))
        self._file(2025, 11, date(2025, 11, 28))
        r = self.g1(2025, 11)
        self.assertEqual(r['b2cs'][0]['txval'], 5000.0)
        self.assertTrue(any('Oct 2025' in w['message'] for w in r['cn_warnings']))

    def test_late_cn_absent_everywhere_including_3b(self):
        old = self.inv(self.customer, "11800.00", price="10000.00", d=date(2024, 6, 1))
        self.inv(self.customer, "5900.00", price="5000.00", d=date(2025, 12, 5))
        self.cn(old, "1000.00", d=date(2025, 12, 10))
        r = self.g1(2025, 12)
        self.assertEqual(r['cdnr'], [])
        self.assertEqual(r['cdnur'], [])
        self.assertEqual(r['bucket_totals']['taxable']['txval'], Decimal('5000.00'))
        self.assertTrue(any(w['code'] == 'cn_after_s34' for w in r['cn_warnings']))

    def _api_cn(self, inv, extra=None):
        payload = {"date": "2025-12-10", "original_invoice": str(inv.id), "customer": str(inv.customer_id),
                   "reason": "discount", "total_amount": "1180.00",
                   "items": [{"product": str(self.product.id), "quantity": 1, "price": "1000.00", "tax": "18.00",
                              "amount": "1180.00"}]}
        payload.update(extra or {})
        return self.client.post("/api/billing/credit-notes/", payload, format="json")

    def test_time_barred_cn_api_prompt_then_commercial_cn_without_gst(self):
        from ledger.models import GeneralLedgerEntry
        old = self.inv(self.customer, "11800.00", price="10000.00", d=date(2024, 6, 1))
        res = self._api_cn(old)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('without GST', str(res.data))
        self.assertIn('offer_without_gst', str(res.data))
        self.customer.refresh_from_db()
        bal0 = self.customer.current_balance
        res = self._api_cn(old, {"without_gst": True})
        self.assertEqual(res.status_code, 201, res.data)
        cn = CreditNote.objects.get(pk=res.data['id'])
        self.assertTrue(cn.gst_time_barred)
        self.assertEqual(cn.total_amount, Decimal('1000.00'))
        gl = GeneralLedgerEntry.objects.filter(credit_note=cn)
        self.assertFalse(gl.filter(description__icontains='GST Reversal').exists())
        self.assertEqual(sum(e.debit for e in gl), sum(e.credit for e in gl))
        self.customer.refresh_from_db()
        self.assertEqual(bal0 - self.customer.current_balance, Decimal('1000.00'))
        r = self.g1(2025, 12)
        self.assertEqual((r['cdnr'], r['cdnur'], r['b2cs']), ([], [], []))
        self.assertEqual(r['bucket_totals']['taxable']['txval'], Decimal('0'))
        self.assertEqual(r['doc_issue']['doc_det'][1]['totnum'], 0)

    def test_without_gst_rejected_when_not_time_barred(self):
        inv = self.inv(self.customer, "11800.00", price="10000.00", d=date(2025, 6, 1))
        res = self._api_cn(inv, {"without_gst": True})
        self.assertEqual(res.status_code, 400, res.data)

    def test_table8_json_flag_and_csv(self):
        from django.test import override_settings
        inv = self.inv(self.ka, "10000.00", price="10000.00", tax="0")
        self.cn(inv, "1000.00", tax="0")
        url = "/api/billing/gst/gstr1-export/?from=2026-09-01&to=2026-09-30"
        res = self.client.get(url)
        self.assertNotIn('nil', res.data)
        self.assertEqual(res.data['_cenvora']['table8'][0]['nil_amt'], 9000.0)
        with override_settings(GSTR1_NIL_JSON_ENABLED=True):
            self.assertEqual(self.client.get(url).data['nil']['inv'][0]['nil_amt'], 9000.0)
        csv_res = self.client.get("/api/billing/gst/gstr1-advances-csv/?from=2026-09-01&to=2026-09-30&sheet=nil")
        body = csv_res.content.decode()
        self.assertIn('Intra-State supplies to unregistered persons,9000.00,0.00,0.00', body)

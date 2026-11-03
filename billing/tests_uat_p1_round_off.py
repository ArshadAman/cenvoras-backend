"""UAT P1 item 4 (Rudra CA): round half up to the rupee, |round_off| <= 0.50, Decimal, own ledger."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from billing.models import SalesInvoice
from billing.round_off import compute_round_off, resolve_apply_round_off
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry


class RoundOffMathTests(SimpleTestCase):
    def test_half_up_and_bounds(self):
        cases = {
            '1157.49': ('1157.00', '-0.49'),
            '1157.50': ('1158.00', '0.50'),
            '1157.51': ('1158.00', '0.49'),
            '1157.99': ('1158.00', '0.01'),
            '1158.00': ('1158.00', '0.00'),
            '0.50': ('1.00', '0.50'),
            '2.5': ('3.00', '0.50'),  # half up, not banker's rounding
        }
        for unrounded, (total, ro) in cases.items():
            t, r = compute_round_off(Decimal(unrounded))
            self.assertEqual((t, r), (Decimal(total), Decimal(ro)), unrounded)
            self.assertLessEqual(abs(r), Decimal('0.50'))
            self.assertIsInstance(r, Decimal)
        self.assertEqual(compute_round_off(Decimal('1157.49'), apply=False), (Decimal('1157.49'), Decimal('0.00')))

    def test_resolve(self):
        class T:  # company setting
            auto_round_off = False
        self.assertTrue(resolve_apply_round_off(T(), True))
        self.assertTrue(resolve_apply_round_off(T(), None, Decimal('1157.99')))
        self.assertFalse(resolve_apply_round_off(T(), None, Decimal('0')))
        self.assertTrue(resolve_apply_round_off(T(), None, None, Decimal('0.20')))
        self.assertFalse(resolve_apply_round_off(T()))
        T.auto_round_off = True
        self.assertTrue(resolve_apply_round_off(T()))


class InvoiceRoundOffApiTests(FourFixesBase):
    def _post(self, **extra):
        payload = {
            "customer_name": self.customer.name, "invoice_date": str(date.today()), "status": "final",
            "total_amount": "0", "place_of_supply": "Karnataka",
            "items": [{"product": str(self.product.id), "quantity": 1, "price": "980.93", "tax": "18.00",
                       "amount": "0", "hsn_sac_code": "847130"}],
        }
        payload.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post("/api/billing/sales-invoices/", payload, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return SalesInvoice.objects.get(pk=res.data["id"])

    def test_client_round_off_value_is_recomputed(self):
        # 980.93 + 18% (176.57) = 1157.50 unrounded. A bogus client value must not be saved.
        inv = self._post(round_off="1157.99")
        self.assertEqual(inv.round_off, Decimal('0.50'))
        self.assertEqual(inv.total_amount, Decimal('1158.00'))

    def test_company_default_on_and_ledger(self):
        inv = self._post()
        self.assertEqual(inv.round_off, Decimal('0.50'))
        entries = GeneralLedgerEntry.objects.filter(sales_invoice=inv)
        ro = entries.filter(account__code='4200')
        self.assertEqual(sum((e.credit - e.debit for e in ro), Decimal('0')), Decimal('0.50'))
        # sales and tax are posted on unrounded values
        sales = entries.filter(account__account_type='revenue').exclude(account__code='4200')
        self.assertEqual(sum((e.credit - e.debit for e in sales), Decimal('0')), Decimal('980.93'))
        debits = sum((e.debit for e in entries), Decimal('0'))
        credits = sum((e.credit for e in entries), Decimal('0'))
        self.assertEqual(debits, credits)

    def test_opt_out_per_invoice_and_company(self):
        inv = self._post(apply_round_off=False)
        self.assertEqual(inv.round_off, Decimal('0.00'))
        self.assertEqual(inv.total_amount, Decimal('1157.50'))
        self.tenant.auto_round_off = False
        self.tenant.save(update_fields=['auto_round_off'])
        inv2 = self._post(invoice_number="")
        self.assertEqual(inv2.round_off, Decimal('0.00'))

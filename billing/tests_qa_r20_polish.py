"""R20: backfill of old advance GST narrations (description only)."""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from billing.models import Payment
from billing import tests_advance_adjustment as _r17
from billing import tests_qa_r19_advances as _r19
from billing.models import Customer
from billing.tests_four_fixes import FourFixesBase
from ledger.models import GeneralLedgerEntry


class R20NarrationTests(FourFixesBase):
    _adv = _r19.R19Tests._adv

    def setUp(self):
        super().setUp()
        self.today = _r17.today_ist()
        self.cust = Customer.objects.create(name="R20 Cust", state="Odisha", created_by=self.tenant)
        self.acc = _r17.AccountingService.get_or_create_default_accounts(self.tenant)

    def test_backfill_dry_run_then_apply_changes_description_only(self):
        p = Payment.objects.get(pk=self._adv(self.cust, "1180.00", "Odisha", ref="R20-REF").data["id"])
        line = GeneralLedgerEntry.objects.get(payment=p, account=self.acc["output_igst"])
        old = f"Output IGST on advance Payment Received {p.id}"
        GeneralLedgerEntry.objects.filter(pk=line.pk).update(description=old)  # pre-#189 narration
        before = GeneralLedgerEntry.objects.filter(pk=line.pk).values("debit", "credit", "date", "account_id", "reference").get()
        out = StringIO()
        call_command("backfill_advance_narrations", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn(f"{p.receipt_voucher_number} / R20-REF", out.getvalue())
        self.assertEqual(GeneralLedgerEntry.objects.get(pk=line.pk).description, old)
        call_command("backfill_advance_narrations", "--apply", stdout=StringIO())
        line.refresh_from_db()
        self.assertEqual(line.description, f"Output IGST on advance {p.receipt_voucher_number} / R20-REF")
        after = GeneralLedgerEntry.objects.filter(pk=line.pk).values("debit", "credit", "date", "account_id", "reference").get()
        self.assertEqual(before, after)

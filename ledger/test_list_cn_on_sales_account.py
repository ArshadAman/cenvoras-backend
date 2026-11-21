from datetime import date
from decimal import Decimal
from io import StringIO
from django.core.management import call_command
from django.test import TestCase


class ListCnOnSalesAccountTest(TestCase):
    def test_lists_legacy_cn_debit_on_4001(self):
        from django.contrib.auth import get_user_model
        from billing.models import Customer
        from billing.models_returns import CreditNote
        from ledger.models import GeneralLedgerEntry
        from ledger.services import AccountingService
        u = get_user_model().objects.create_user(username='cnr', email='cnr@x.in', password='x')
        acc = AccountingService.get_or_create_default_accounts(u)
        c = Customer.objects.create(name='C', created_by=u)
        cn = CreditNote.objects.create(credit_note_number='CN-L1', date=date(2026, 10, 5), customer=c,
                                       total_amount=Decimal('10500'), created_by=u)
        GeneralLedgerEntry.objects.create(date=cn.date, account=acc['sales_revenue'], debit=Decimal('10500'),
                                          credit=0, description='legacy', credit_note=cn, created_by=u)
        out = StringIO()
        call_command('list_cn_on_sales_account', '--from', '2026-10-01', '--to', '2026-10-31', stdout=out)
        self.assertIn('CN-L1', out.getvalue())
        self.assertIn('1 entries', out.getvalue())
        self.assertEqual(GeneralLedgerEntry.objects.count(), 1)

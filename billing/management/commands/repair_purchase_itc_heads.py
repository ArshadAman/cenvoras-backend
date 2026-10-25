"""
Rebuild purchase-bill Input GST journals using vendor state vs place of supply.

Use after R15-11 so 4A is no longer split 5,400/5,400/5,400 on all-IGST bills.

  python manage.py repair_purchase_itc_heads --tenant <username>
  CENVORA_ALLOW_LEDGER_CORRECTION=yes python manage.py repair_purchase_itc_heads --tenant <username> --apply
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q


class Command(BaseCommand):
    help = 'Rebuild purchase-bill Input GST ledger heads from vendor GSTIN/state vs POS.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        from billing.models import PurchaseBill
        from ledger.models import GeneralLedgerEntry
        from ledger.services import AccountingService

        User = get_user_model()
        ident = opts['tenant']
        q = Q(username=ident) | Q(email__iexact=ident)
        if str(ident).isdigit():
            q |= Q(pk=int(ident))
        users = list(User.objects.filter(q)[:2])
        if len(users) != 1:
            raise CommandError(f'Tenant {ident!r} not found or ambiguous.')
        tenant = users[0]

        bills = PurchaseBill.objects.filter(created_by=tenant).prefetch_related('items')
        planned = 0
        for bill in bills:
            gst_entries = GeneralLedgerEntry.objects.filter(
                purchase_bill=bill, account__code__in=['1301', '1302', '1303']
            )
            if not gst_entries.exists() and not bill.items.exists():
                continue
            planned += 1
            self.stdout.write(f"  {bill.bill_number}  {gst_entries.count()} GST lines")

        if not opts['apply']:
            self.stdout.write(self.style.WARNING(f'Dry-run: {planned} bills would be rebuilt. Pass --apply.'))
            return
        if os.environ.get('CENVORA_ALLOW_LEDGER_CORRECTION', '').lower() != 'yes':
            raise CommandError('Refusing --apply without CENVORA_ALLOW_LEDGER_CORRECTION=yes.')

        rebuilt = 0
        with transaction.atomic():
            for bill in bills:
                GeneralLedgerEntry.objects.filter(purchase_bill=bill).delete()
                AccountingService.create_purchase_bill_entries(bill)
                rebuilt += 1
        self.stdout.write(self.style.SUCCESS(f'Rebuilt {rebuilt} purchase bills.'))

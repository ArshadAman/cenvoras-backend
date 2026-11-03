"""Read-only: credit notes whose sales-return debit sits on Sales Revenue (4001) instead of
Sales Return (4002). Credit notes posted before 13 Oct 2026 (aa37b12) debited 4001 directly,
so the P&L shows Sales and Sales Return each lower by that amount (net unchanged).
Changes nothing. Usage: python manage.py list_cn_on_sales_account [--from 2026-10-01 --to 2026-10-31]
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum


class Command(BaseCommand):
    help = 'Read-only list of credit notes debited to Sales Revenue (4001) instead of Sales Return (4002).'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='date_from')
        parser.add_argument('--to', dest='date_to')

    def handle(self, *args, **opts):
        from ledger.models import GeneralLedgerEntry
        qs = GeneralLedgerEntry.objects.filter(credit_note__isnull=False, account__code='4001', debit__gt=0)
        if opts.get('date_from'):
            qs = qs.filter(date__gte=opts['date_from'])
        if opts.get('date_to'):
            qs = qs.filter(date__lte=opts['date_to'])
        rows = qs.select_related('credit_note', 'created_by').order_by('created_by_id', 'date')
        n = 0
        for e in rows:
            n += 1
            cn = e.credit_note
            self.stdout.write(f"{getattr(e.created_by, 'email', e.created_by_id)}\t{e.date}\t{cn.credit_note_number}\t"
                              f"debit 4001 Rs {e.debit}\tinvoice {getattr(cn.original_invoice, 'invoice_number', '-')}")
        total = qs.aggregate(s=Sum('debit'))['s'] or 0
        self.stdout.write(f"{n} entries, total Rs {total}. Read-only: nothing changed.")

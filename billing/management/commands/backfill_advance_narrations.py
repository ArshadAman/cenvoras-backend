"""
DRY RUN by default: list advance GST journal lines whose narration still shows the internal
'Payment Received <uuid>' and the new text (RV number / payment reference, as for new entries since #189).
--apply updates ONLY the description text. Amounts, dates, accounts and references are never touched.
Live data: run --apply only with the owner's approval.
"""
import re

from django.core.management.base import BaseCommand

PATTERN = re.compile(r'^Output (IGST|CGST|SGST) on advance Payment Received [0-9a-fA-F-]{36}\s*$')


class Command(BaseCommand):
    help = "Dry-run (default) or --apply: replace 'Payment Received <uuid>' in advance GST narrations."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--tenant', help='Limit to one tenant (user id).')

    def handle(self, *args, **opts):
        from ledger.models import GeneralLedgerEntry
        from ledger.services import _adv_label
        qs = (GeneralLedgerEntry.objects.filter(description__startswith='Output ', payment__isnull=False,
                                                description__contains='on advance Payment Received ')
              .select_related('payment'))
        if opts.get('tenant'):
            qs = qs.filter(created_by_id=opts['tenant'])
        apply = opts['apply']
        self.stdout.write(('APPLY' if apply else 'DRY RUN') + ': advance GST narrations')
        n = 0
        for line in qs.order_by('date', 'created_at'):
            m = PATTERN.match(line.description or '')
            if not m:
                continue
            new = f"Output {m.group(1)} on advance {_adv_label(line.payment)}"
            n += 1
            self.stdout.write(f"  {line.date} {line.id}: '{line.description}' -> '{new}'")
            if apply:
                GeneralLedgerEntry.objects.filter(pk=line.pk).update(description=new)
        self.stdout.write(f"{n} line(s) " + ('updated' if apply else 'would be updated (dry run: nothing changed)'))

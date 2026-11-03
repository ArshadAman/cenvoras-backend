"""Read-only: credit notes linked to an invoice that is not final (draft/cancelled).
Rudra CA (#222 follow-up): s.34 applies only to an issued invoice. Changes nothing.
Usage: python manage.py list_cns_linked_to_drafts [--tenant <email or id>]
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Read-only list of credit notes linked to non-final (draft) invoices.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Tenant email or user id')

    def handle(self, *args, **opts):
        from billing.models_returns import CreditNote
        qs = (CreditNote.objects.filter(original_invoice__isnull=False)
              .exclude(original_invoice__status='final')
              .select_related('original_invoice', 'created_by', 'customer').order_by('created_by_id', 'date'))
        t = opts.get('tenant')
        if t:
            qs = qs.filter(created_by__email=t) if '@' in t else qs.filter(created_by_id=t)
        n = 0
        for cn in qs:
            n += 1
            inv = cn.original_invoice
            self.stdout.write(f"{getattr(cn.created_by, 'email', cn.created_by_id)}\t{cn.credit_note_number}\t{cn.date}\t"
                              f"Rs {cn.total_amount}\tinvoice {inv.invoice_number} ({inv.status})")
        self.stdout.write(f"{n} credit notes linked to non-final invoices. Read-only: nothing changed.")

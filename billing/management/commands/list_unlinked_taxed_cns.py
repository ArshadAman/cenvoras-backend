"""
UAT P1 #12 (Rudra CA): READ-ONLY list of existing credit notes that carry GST but are
not linked to an original invoice, per tenant, so they can be fixed (link the invoice,
or re-issue as 'Credit note without GST'). This command never writes anything.

    python manage.py list_unlinked_taxed_cns [--tenant <id or email>]
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from billing.models_returns import CreditNote


class Command(BaseCommand):
    help = "List unlinked credit notes that carry GST (read-only, no writes)."

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Tenant user id or email (default: all tenants).')

    def handle(self, *args, **opts):
        qs = (CreditNote.objects
              .filter(original_invoice__isnull=True, gst_time_barred=False, items__tax__gt=0)
              .select_related('created_by', 'customer').distinct()
              .order_by('created_by_id', 'date', 'credit_note_number'))
        t = opts.get('tenant')
        if t:
            qs = qs.filter(Q(created_by__email__iexact=t) | Q(created_by__id__iexact=t))
        current, count = None, 0
        for cn in qs:
            if cn.created_by_id != current:
                current = cn.created_by_id
                biz = getattr(cn.created_by, 'business_name', '') or cn.created_by.email
                self.stdout.write(f"\nTenant {biz} ({cn.created_by_id})")
            gstin = (getattr(cn.customer, 'gstin', '') or '').strip() or 'unregistered'
            self.stdout.write(f"  {cn.credit_note_number}  {cn.date:%d-%m-%Y}  {cn.customer.name} [{gstin}]  "
                              f"Rs {Decimal(str(cn.total_amount)):,.2f}")
            count += 1
        self.stdout.write(f"\n{count} unlinked taxed credit note(s). Read-only: nothing was changed.")

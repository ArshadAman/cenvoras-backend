"""
Rudra (#212): READ-ONLY admin list of saved GSTINs that fail the full check (state code,
check character). They stay accepted while unchanged and show 'Invalid GSTIN, please correct'.
This command never writes anything.

    python manage.py list_invalid_gstins [--tenant <id or email>]
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q

from billing.gst_utils import is_valid_gstin
from billing.models import Customer, Vendor


def invalid_gstin_rows(tenant=None):
    """Yield (tenant_id, kind, record_id, name, gstin) for saved GSTINs that fail the check."""
    User = get_user_model()
    users = User.objects.exclude(gstin__isnull=True).exclude(gstin='')
    if tenant:
        users = users.filter(Q(email__iexact=tenant) | Q(id__iexact=tenant))
    for u in users.order_by('id'):
        if not is_valid_gstin(u.gstin):
            yield (u.id, 'company', u.id, u.business_name or u.email, u.gstin)
    for model, kind in ((Customer, 'customer'), (Vendor, 'vendor')):
        qs = model.objects.exclude(gstin__isnull=True).exclude(gstin='')
        if tenant:
            qs = qs.filter(Q(created_by__email__iexact=tenant) | Q(created_by__id__iexact=tenant))
        for p in qs.order_by('created_by_id', 'name'):
            if not is_valid_gstin(p.gstin):
                yield (p.created_by_id, kind, p.id, p.name, p.gstin)


class Command(BaseCommand):
    help = "List saved GSTINs that fail the checksum/state check (read-only, no writes)."

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Tenant user id or email (default: all tenants).')

    def handle(self, *args, **opts):
        count = 0
        for tenant_id, kind, rid, name, gstin in invalid_gstin_rows(opts.get('tenant')):
            self.stdout.write(f"tenant {tenant_id}  {kind:<8} {rid}  {name}  [{gstin}]")
            count += 1
        self.stdout.write(f"\n{count} invalid GSTIN(s). Read-only: nothing was changed.")

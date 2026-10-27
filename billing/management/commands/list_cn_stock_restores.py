"""
READ-ONLY. Lists credit notes whose reason is Post-Sale Discount, Rate Difference or Other but which
put goods back into stock (Rudra CA, R21). Makes no writes and has no --apply flag; any correction
needs Cenvora's approval.

How "restored stock" is known:
  * CNs created after R21 store `stock_restored` (True/False).
  * Older CNs have stock_restored = NULL. The old create code (billing/returns_views.py) restored
    every whole-unit line for every reason, so those are reported as "inferred".
  There is no stock-movement table: stock was changed in place (Product.stock, and StockPoint for
  batch lines), and the CN journal (ledger) has no inventory lines. So the "movement" column shows
  the batch StockPoint id where one applies, else "n/a (in-place Product.stock update)".

Usage:  python manage.py list_cn_stock_restores [--tenant <email or id>] [--csv]
Single database (no per-tenant schemas); output is grouped by tenant.
"""
import csv
import sys
from decimal import Decimal

from django.core.management.base import BaseCommand

NON_STOCK_REASONS = ('discount', 'rate_diff', 'other')


class Command(BaseCommand):
    help = 'READ-ONLY: list discount/rate-difference/other credit notes that restored stock.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Limit to one tenant (email, username or id).')
        parser.add_argument('--csv', action='store_true', help='CSV output.')

    def handle(self, *args, **opts):
        from django.db.models import Q
        from billing.models_returns import CreditNote
        from inventory.models import StockPoint

        qs = (CreditNote.objects.filter(reason__in=NON_STOCK_REASONS)
              .filter(Q(stock_restored=True) | Q(stock_restored__isnull=True))
              .select_related('created_by', 'warehouse').prefetch_related('items__product', 'items__batch')
              .order_by('created_by_id', 'date', 'credit_note_number'))
        t = opts.get('tenant')
        if t:
            cond = Q(created_by__email__iexact=t) | Q(created_by__username__iexact=t)
            if str(t).replace('-', '').isalnum() and len(str(t)) >= 32:
                cond |= Q(created_by_id=t)
            qs = qs.filter(cond)

        cols = ['tenant', 'cn_number', 'cn_date', 'reason', 'item', 'qty_restored', 'basis', 'stock_movement']
        rows = []
        for cn in qs:
            tenant = getattr(cn.created_by, 'email', None) or str(cn.created_by_id)
            basis = 'recorded' if cn.stock_restored else 'inferred (pre-R21 code always restored)'
            for it in cn.items.all():
                q = Decimal(str(it.quantity))
                if q <= 0 or q != q.to_integral_value():
                    continue  # old code only moved whole units; fractional lines never touched stock
                movement = 'n/a (in-place Product.stock update)'
                if it.batch_id and cn.warehouse_id:
                    sp = StockPoint.objects.filter(batch_id=it.batch_id, warehouse_id=cn.warehouse_id).only('id').first()
                    if sp:
                        movement = f'StockPoint {sp.id} (+ Product.stock)'
                rows.append([tenant, cn.credit_note_number, cn.date.isoformat(), cn.reason,
                             getattr(it.product, 'name', str(it.product_id)), str(int(q)), basis, movement])

        if opts.get('csv'):
            w = csv.writer(self.stdout)
            w.writerow(cols)
            w.writerows(rows)
        else:
            if not rows:
                self.stdout.write('No discount / rate-difference / other credit notes restored stock.')
            last = None
            for r in rows:
                if r[0] != last:
                    self.stdout.write(f'\n== Tenant: {r[0]}')
                    last = r[0]
                self.stdout.write(f'  {r[1]}  {r[2]}  {r[3]:<9}  {r[4]}  qty {r[5]}  [{r[6]}]  {r[7]}')
            self.stdout.write(f'\n{len(rows)} line(s). READ-ONLY: nothing was changed.')

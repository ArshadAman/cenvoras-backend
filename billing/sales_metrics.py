"""
Single source of truth for sales KPIs used by dashboard, AI, and sales analytics.

Definition (locked for QA parity):
- Scope: tenant + team (created_by = tenant OR created_by.parent = tenant)
- Status: final only (excludes draft / cancelled)
- Period: IST calendar month start through today IST (when MTD)
- Amount: invoice total_amount (incl. GST) minus credit notes in the same window
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum


def _ist_today() -> date:
    try:
        from zoneinfo import ZoneInfo
        from django.utils import timezone
        return timezone.now().astimezone(ZoneInfo('Asia/Kolkata')).date()
    except Exception:
        from django.utils import timezone
        return timezone.localdate()


def _tenant_scope_q(tenant):
    return Q(created_by=tenant) | Q(created_by__parent=tenant)


def aggregate_sales_metric(tenant, *, date_from=None, date_to=None, mtd=False):
    """
    Returns dict: gross, returns, net, count, date_from, date_to, label.
    If mtd=True, date_from/date_to are set to IST month bounds (ignores passed dates).
    """
    from billing.models import SalesInvoice
    from billing.models_returns import CreditNote

    today = _ist_today()
    if mtd:
        date_from = today.replace(day=1)
        date_to = today

    inv_qs = SalesInvoice.objects.filter(_tenant_scope_q(tenant), status='final')
    cn_qs = CreditNote.objects.filter(_tenant_scope_q(tenant))

    if date_from is not None:
        inv_qs = inv_qs.filter(invoice_date__gte=date_from)
        cn_qs = cn_qs.filter(date__gte=date_from)
    if date_to is not None:
        inv_qs = inv_qs.filter(invoice_date__lte=date_to)
        cn_qs = cn_qs.filter(date__lte=date_to)

    gross = inv_qs.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    returns = cn_qs.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    net = (Decimal(str(gross)) - Decimal(str(returns))).quantize(Decimal('0.01'))
    count = inv_qs.count()

    label = (
        "This month's sales (incl. GST, net of credit notes)"
        if mtd
        else "Sales (incl. GST, net of credit notes)"
    )
    return {
        'gross': float(Decimal(str(gross)).quantize(Decimal('0.01'))),
        'returns': float(Decimal(str(returns)).quantize(Decimal('0.01'))),
        'net': float(net),
        'count': count,
        'date_from': str(date_from) if date_from else None,
        'date_to': str(date_to) if date_to else None,
        'label': label,
        'includes_gst': True,
        'credit_notes_subtracted': True,
        'status_filter': 'final',
    }


def sales_mtd(tenant):
    return aggregate_sales_metric(tenant, mtd=True)

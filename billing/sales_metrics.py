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
    taxable_net = _taxable_net(inv_qs, cn_qs, tenant)

    label = (
        "This month's sales (incl. GST, net of credit notes)"
        if mtd
        else "Sales (incl. GST, net of credit notes)"
    )
    return {
        'gross': float(Decimal(str(gross)).quantize(Decimal('0.01'))),
        'returns': float(Decimal(str(returns)).quantize(Decimal('0.01'))),
        'net': float(net),
        'taxable_net': float(taxable_net),
        'count': count,
        'date_from': str(date_from) if date_from else None,
        'date_to': str(date_to) if date_to else None,
        'label': label,
        'includes_gst': True,
        'credit_notes_subtracted': True,
        'status_filter': 'final',
    }


def _taxable_net(inv_qs, cn_qs, tenant):
    """Invoice taxable (excl. GST) minus matching credit notes — Tax Register / 3.1(a) txval."""
    from billing.gst_utils import (
        calculate_item_tax_breakup,
        credit_note_tax_breakup,
        get_pos_state_code,
        get_seller_state_code,
    )

    seller = get_seller_state_code(tenant)
    taxable = Decimal('0.00')
    for inv in inv_qs.select_related('customer').prefetch_related('items'):
        pos = inv.place_of_supply or (inv.customer.state if inv.customer else None)
        pos_code = get_pos_state_code(pos, fallback_state=seller)
        is_inter = seller != pos_code
        for item in inv.items.all():
            if getattr(item, 'row_type', 'item') == 'note':
                continue
            line_taxable, *_rest = calculate_item_tax_breakup(item, is_inter)
            taxable += line_taxable
    for cn in cn_qs.select_related('customer', 'original_invoice').prefetch_related('items'):
        if cn.original_invoice_id and getattr(cn.original_invoice, 'status', None) != 'final':
            continue
        brk = credit_note_tax_breakup(cn, seller)
        taxable -= brk['totals']['taxable']
    return taxable.quantize(Decimal('0.01'))


def sales_mtd(tenant):
    return aggregate_sales_metric(tenant, mtd=True)

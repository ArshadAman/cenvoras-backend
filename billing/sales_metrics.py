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


def sales_register_invoices(tenant, date_from=None, date_to=None):
    """Final sales invoices in the Sales Tax Register (shared by the register and AI month sales)."""
    from billing.models import SalesInvoice
    qs = SalesInvoice.objects.filter(created_by=tenant, status='final')
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    return qs


def sales_register_credit_notes(tenant, date_from=None, date_to=None):
    """
    Credit notes in the Sales Tax Register, by CN date: only CNs linked to a final invoice.
    Unlinked CNs (legacy taxed CNs listed for fixing under #207, or credit notes without GST)
    are not in any GST table and are not subtracted from taxable sales.
    """
    from billing.models_returns import CreditNote
    qs = CreditNote.objects.filter(created_by=tenant, original_invoice__isnull=False,
                                   original_invoice__status='final')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return qs


def sales_mtd(tenant):
    return aggregate_sales_metric(tenant, mtd=True)


# ---------------------------------------------------------------------------
# UAT P1 item 3 (Rudra CA): sales for a named calendar month.
#   Sales(M) = taxable value (excl. GST) of final invoices dated in M
#              - taxable value of credit notes dated in M
#              + debit notes dated in M
# Month boundaries are IST calendar dates (invoice_date / CN date are DateFields, so no
# UTC conversion is involved); months are 1-based. There is no customer-side debit note
# model in Cenvora (DebitNote is a purchase return), so the DN term is 0.
# ---------------------------------------------------------------------------
import calendar as _calendar
import re as _re

_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[_calendar.month_name[_i].lower()] = _i
    _MONTHS[_calendar.month_abbr[_i].lower()] = _i
_MONTHS['sept'] = 9
_MONTH_RE = _re.compile(
    r'\b(' + '|'.join(sorted(_MONTHS, key=len, reverse=True)) + r')\b(?:[\s,\'-]*((?:19|20)\d{2}))?',
    _re.IGNORECASE)


def ist_month_bounds(year: int, month: int):
    """First and last IST calendar date of a 1-based month."""
    if not 1 <= int(month) <= 12:
        raise ValueError('month must be 1..12')
    last = _calendar.monthrange(int(year), int(month))[1]
    return date(int(year), int(month), 1), date(int(year), int(month), last)


def resolve_month_reference(text: str, today: date | None = None):
    """
    Find a month name in `text` -> (year, month) or None.
    'October 2025' -> (2025, 10). A month with no year means the most recent one that has
    started: in Nov 2026, 'October' -> Oct 2026, 'December' -> Dec 2025, 'November' -> Nov 2026
    (month to date).
    """
    if not text:
        return None
    today = today or _ist_today()
    for m in _MONTH_RE.finditer(text):
        word = m.group(1).lower()
        if word == 'may' and not m.group(2) and not _re.search(r'\bmay\b\s*(sales?|month)|\bin may\b|\bof may\b|\bfor may\b', text, _re.I):
            continue  # "may" as a verb
        month = _MONTHS[word]
        if m.group(2):
            return int(m.group(2)), month
        year = today.year if month <= today.month else today.year - 1
        return year, month
    return None


def sales_for_month(tenant, year: int, month: int):
    """Sales(M) per Rudra's rule, as Decimal plus its components."""
    from billing.models import SalesInvoice
    from billing.models_returns import CreditNote

    start, end = ist_month_bounds(year, month)
    # Same document sets as the Sales Tax Register (retest item 3).
    inv_qs = sales_register_invoices(tenant, start, end)
    no_cn = CreditNote.objects.none()
    cn_qs = sales_register_credit_notes(tenant, start, end)
    invoice_taxable = _taxable_net(inv_qs, no_cn, tenant)
    cn_taxable = (Decimal('0.00') - _taxable_net(SalesInvoice.objects.none(), cn_qs, tenant))
    debit_notes = Decimal('0.00')
    total = (invoice_taxable - cn_taxable + debit_notes).quantize(Decimal('0.01'))
    return {
        'year': int(year), 'month': int(month),
        'date_from': start, 'date_to': end,
        'label': f"{_calendar.month_name[int(month)]} {int(year)}",
        'invoice_taxable': invoice_taxable,
        'credit_note_taxable': cn_taxable.quantize(Decimal('0.01')),
        'debit_notes': debit_notes,
        'sales': total,
        'invoice_count': inv_qs.count(),
        'credit_note_count': cn_qs.count(),
    }


def answer_month_sales_question(question: str, tenant, today: date | None = None):
    """Deterministic answer for 'sales in <month>' questions, else None."""
    if not question or not _re.search(r'\bsal(e|es)\b|\bturnover\b|\brevenue\b', question, _re.I):
        return None
    ref = resolve_month_reference(question, today)
    if not ref:
        return None
    r = sales_for_month(tenant, *ref)
    return (
        f"**Sales for {r['label']}** ({r['date_from']:%d %b %Y} to {r['date_to']:%d %b %Y}): "
        f"**₹{r['sales']:,.2f}** taxable value, excluding GST.\n\n"
        f"• Invoices: ₹{r['invoice_taxable']:,.2f} ({r['invoice_count']} final invoices)\n"
        f"• Less credit notes: ₹{r['credit_note_taxable']:,.2f} ({r['credit_note_count']})\n"
        f"Drafts are excluded."
    )

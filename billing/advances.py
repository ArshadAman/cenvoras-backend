"""
Customer advances: applying them to invoices without rewriting the receipt journals.

Receipt (unchanged, posted once by create_payment_received_entries):
    service: Dr Bank/Cash gross; Cr 2102 taxable; Cr Output CGST/SGST (or IGST)
    goods:   Dr Bank/Cash gross; Cr 2102 gross (no GST, Notfn 66/2017-CT)
Application (this module, dated on the application date):
    Dr 2102 taxable part; Dr Output CGST/SGST/IGST part; Cr Accounts Receivable gross part
    (goods: Dr 2102 gross; Cr AR gross). Partial applications are pro rata; the last one takes
    the exact remainder so the advance clears to the paisa.
De-application: a mirror entry dated the de-application date. Nothing is deleted.
"""
from datetime import date as date_cls
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

ZERO = Decimal('0.00')
CENT = Decimal('0.01')


def _q(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def today_ist():
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    return timezone.now().astimezone(ZoneInfo('Asia/Kolkata')).date()


def adjustment_reference(adj_id):
    return f"Advance Adjustment {adj_id}"


def posted_advance_split(payment):
    """Gross/taxable/tax actually posted at receipt (read from the GL, not re-inferred)."""
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService
    accounts = AccountingService.get_or_create_default_accounts(payment.created_by)
    lines = GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}", created_by=payment.created_by)
    by = lambda acc: sum((l.credit - l.debit for l in lines if acc and l.account_id == acc.id), ZERO)
    adv = by(accounts.get('customer_advances') or accounts.get('2102'))
    cgst = by(accounts.get('output_cgst'))
    sgst = by(accounts.get('output_sgst'))
    igst = by(accounts.get('output_igst'))
    gross = _q(payment.amount or 0)
    if not lines:
        # No receipt journal (should not happen); fall back to the inference used at receipt.
        from billing.gst_rules import advance_tax_split
        from billing.gst_utils import get_seller_state_code
        sp = advance_tax_split(payment, get_seller_state_code(payment.created_by))
        adv, cgst, sgst, igst = sp['taxable'], sp['cgst'], sp['sgst'], sp['igst']
    tax = cgst + sgst + igst
    taxable = adv if tax > 0 else gross
    rate = _q(tax * 100 / taxable) if (tax > 0 and taxable > 0) else ZERO
    # Snap to the nearest standard GST rate (rounding on small amounts)
    for std in (Decimal('0.25'), Decimal('3'), Decimal('5'), Decimal('12'), Decimal('18'), Decimal('28')):
        if abs(rate - std) <= Decimal('0.1'):
            rate = std.quantize(CENT)
            break
    return {'gross': gross, 'taxable': _q(taxable), 'cgst': _q(cgst), 'sgst': _q(sgst), 'igst': _q(igst),
            'tax': _q(tax), 'rate': rate, 'kind': 'services' if tax > 0 else 'goods',
            'is_interstate': igst > 0}


def active_adjustments(payment):
    return [a for a in payment.adjustments.all() if a.reversed_on is None]


def applied_amount(payment):
    return sum((a.gross for a in active_adjustments(payment)), ZERO)


def unapplied_amount(payment):
    if payment.invoice_id:
        return ZERO
    return _q(Decimal(payment.amount or 0) - applied_amount(payment))


def invoice_due(invoice):
    return _q(Decimal(invoice.total_amount or 0) - Decimal(invoice.amount_paid or 0))


def _post(adj, sign, on_date, description):
    """sign=+1 application, -1 mirror reversal. Idempotent per (adjustment, sign)."""
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService
    ref = adjustment_reference(adj.id) + (' (reversal)' if sign < 0 else '')
    if GeneralLedgerEntry.objects.filter(reference=ref, created_by=adj.created_by).exists():
        return False
    accounts = AccountingService.get_or_create_default_accounts(adj.created_by)
    pay = adj.payment
    common = dict(date=on_date, reference=ref, customer=pay.customer, payment=pay, created_by=adj.created_by,
                  description=description)

    def line(account, dr, cr):
        if dr == 0 and cr == 0:
            return
        if sign < 0:
            dr, cr = cr, dr
        GeneralLedgerEntry.objects.create(account=account, debit=dr, credit=cr, **common)

    line(accounts.get('customer_advances') or accounts['2102'], adj.taxable, ZERO)
    line(accounts['output_cgst'], adj.cgst, ZERO)
    line(accounts['output_sgst'], adj.sgst, ZERO)
    line(accounts['output_igst'], adj.igst, ZERO)
    line(accounts['accounts_receivable'], ZERO, adj.gross)
    return True


def _check_open_period(on_date):
    today = today_ist()
    if on_date > today:
        raise ValueError('Application date cannot be in the future.')
    if (on_date.year, on_date.month) != (today.year, today.month):
        raise ValueError('Application date must be in the current month; earlier months are not rewritten.')


@transaction.atomic
def apply_advance(payment, invoice, amount=None, on_date=None):
    """Apply (part of) an advance to a final invoice. Returns the AdvanceAdjustment (or None if nothing to do)."""
    from billing.models import AdvanceAdjustment, Payment
    from billing.balance_sync import recompute_customer_balance, recompute_invoice_amount_paid

    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    on_date = on_date or today_ist()
    if isinstance(on_date, str):
        on_date = date_cls.fromisoformat(on_date)
    _check_open_period(on_date)
    if payment.invoice_id:
        raise ValueError('This receipt is already linked to an invoice; it is not an advance.')
    if invoice.status != 'final':
        raise ValueError('Advances can only be applied to final invoices.')
    if invoice.customer_id != payment.customer_id:
        raise ValueError('Invoice does not belong to this customer.')
    if on_date < payment.date or on_date < invoice.invoice_date:
        raise ValueError('Application date cannot be before the advance or the invoice date.')

    left = unapplied_amount(payment)
    due = invoice_due(invoice)
    amt = _q(amount) if amount is not None else min(left, due)
    amt = min(amt, left, due)
    if amt <= 0:
        return None

    split = posted_advance_split(payment)
    done = list(active_adjustments(payment))
    if amt == left:
        # Last slice: exact remainder of what was posted, so 2102 and output tax clear to zero.
        taxable = split['taxable'] - sum((a.taxable for a in done), ZERO)
        cgst = split['cgst'] - sum((a.cgst for a in done), ZERO)
        sgst = split['sgst'] - sum((a.sgst for a in done), ZERO)
        igst = split['igst'] - sum((a.igst for a in done), ZERO)
    else:
        ratio = amt / split['gross'] if split['gross'] else ZERO
        cgst, sgst, igst = (_q(split[k] * ratio) for k in ('cgst', 'sgst', 'igst'))
        taxable = amt - cgst - sgst - igst

    adj = AdvanceAdjustment.objects.create(
        payment=payment, invoice=invoice, date=on_date, gross=amt, taxable=_q(taxable),
        cgst=_q(cgst), sgst=_q(sgst), igst=_q(igst), rate=split['rate'], created_by=payment.created_by)
    _post(adj, +1, on_date, f"Advance applied to {invoice.invoice_number}")
    if not payment.adjusted_on:
        payment.adjusted_on = on_date
        Payment.objects.filter(pk=payment.pk).update(adjusted_on=on_date)
    recompute_invoice_amount_paid(invoice.id)
    recompute_customer_balance(payment.customer_id)
    return adj


@transaction.atomic
def reverse_adjustment(adj, on_date=None):
    """Undo an application with a mirror entry dated on_date (current month). Idempotent."""
    from billing.models import AdvanceAdjustment
    from billing.balance_sync import recompute_customer_balance, recompute_invoice_amount_paid
    adj = AdvanceAdjustment.objects.select_for_update().get(pk=adj.pk)
    if adj.reversed_on:
        return adj
    on_date = on_date or today_ist()
    if isinstance(on_date, str):
        on_date = date_cls.fromisoformat(on_date)
    _check_open_period(on_date)
    if on_date < adj.date:
        raise ValueError('Reversal date cannot be before the application date.')
    _post(adj, -1, on_date, f"Advance un-applied from {adj.invoice.invoice_number}")
    adj.reversed_on = on_date
    adj.save(update_fields=['reversed_on'])
    recompute_invoice_amount_paid(adj.invoice_id)
    recompute_customer_balance(adj.payment.customer_id)
    return adj


# ─── GSTR-1 at / atadj (Rudra CA rule) ────────────────────────────

def _in(d, lo, hi):
    return d is not None and lo <= d <= hi


def advance_tables(tenant, from_date, to_date, seller_state_code):
    """
    Per service advance, for period P = [from_date, to_date]:
      applied_in_P = applications dated in P minus reversals dated in P (taxable/tax, signed)
      received in P  -> at = receipt - applied_in_P ; no atadj   (same-period advance+invoice nets out)
      received before-> applied_in_P > 0 -> atadj ; < 0 (un-applied) -> at again
    Goods advances never appear. ad_amt is TAXABLE value. Legacy receipts already linked to an
    invoice (old flow) are plain invoice receipts and are not reported.
    Returns (at_rows, atadj_rows, at_totals, atadj_totals); rows are flat, see group_for_portal().
    """
    from billing.models import AdvanceAdjustment, Payment
    from billing.gst_utils import get_pos_state_code
    lo, hi = _d(from_date), _d(to_date)
    zero = lambda: {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO}
    at_tot, adj_tot = zero(), zero()
    at_rows, adj_rows = [], []

    candidates = set(Payment.objects.filter(created_by=tenant, invoice__isnull=True, customer__isnull=False,
                                            date__gte=lo, date__lte=hi).values_list('id', flat=True))
    adjs = AdvanceAdjustment.objects.filter(created_by=tenant).filter(
        models_q(date__gte=lo, date__lte=hi) | models_q(reversed_on__gte=lo, reversed_on__lte=hi))
    candidates |= set(adjs.values_list('payment_id', flat=True))

    for p in Payment.objects.filter(id__in=candidates).select_related('customer'):
        split = posted_advance_split(p)
        if split['kind'] != 'services':
            continue
        app = {'taxable': ZERO, 'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO}
        for a in p.adjustments.all():
            if _in(a.date, lo, hi):
                for k in app:
                    app[k] += getattr(a, k)
            if _in(a.reversed_on, lo, hi):
                for k in app:
                    app[k] -= getattr(a, k)
        if _in(p.date, lo, hi):
            vals = {k: split[k] - app[k] for k in app}
            target = 'at'
        elif any(v > 0 for v in app.values()):
            vals, target = app, 'atadj'
        elif any(v < 0 for v in app.values()):
            vals, target = {k: -v for k, v in app.items()}, 'at'
        else:
            continue
        if vals['taxable'] <= 0:
            continue
        pos_raw = getattr(p, 'place_of_supply', None) or (p.customer.state if p.customer else None)
        pos = get_pos_state_code(pos_raw, fallback_state=seller_state_code) if pos_raw else seller_state_code
        if split['is_interstate'] and pos == seller_state_code:
            pos = '97'
        row = {'sply_ty': 'INTER' if split['is_interstate'] else 'INTRA', 'pos': pos, 'rt': float(split['rate']),
               'ad_amt': float(vals['taxable']), 'camt': float(vals['cgst']), 'samt': float(vals['sgst']),
               'iamt': float(vals['igst']), 'csamt': 0.0, 'payment_id': str(p.id)}
        rows, tot = (at_rows, at_tot) if target == 'at' else (adj_rows, adj_tot)
        rows.append(row)
        tot['txval'] += vals['taxable']
        tot['camt'] += vals['cgst']
        tot['samt'] += vals['sgst']
        tot['iamt'] += vals['igst']
    return at_rows, adj_rows, at_tot, adj_tot


def group_for_portal(rows):
    """Offline-tool shape: [{pos, sply_ty, itms:[{rt, ad_amt, iamt|camt+samt, csamt}]}]. VERIFY against the current schema."""
    groups = {}
    for r in rows:
        g = groups.setdefault((r['pos'], r['sply_ty']), {})
        it = g.setdefault(r['rt'], {'rt': r['rt'], 'ad_amt': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO, 'csamt': ZERO})
        for k in ('ad_amt', 'iamt', 'camt', 'samt'):
            it[k] += Decimal(str(r[k]))
    out = []
    for (pos, sply), items in sorted(groups.items()):
        itms = []
        for it in items.values():
            d = {'rt': it['rt'], 'ad_amt': float(_q(it['ad_amt']))}
            if sply == 'INTER':
                d['iamt'] = float(_q(it['iamt']))
            else:
                d['camt'] = float(_q(it['camt']))
                d['samt'] = float(_q(it['samt']))
            d['csamt'] = 0.0
            itms.append(d)
        out.append({'pos': pos, 'sply_ty': sply, 'itms': itms})
    return out


def _d(v):
    if isinstance(v, date_cls):
        return v
    return date_cls.fromisoformat(str(v)[:10])


def models_q(**kw):
    from django.db.models import Q
    return Q(**kw)

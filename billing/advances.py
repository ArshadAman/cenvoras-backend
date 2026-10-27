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
Dates: allowed in any period whose GSTR-1 is not marked filed (GSTR1Filing). The accounting date of an
application follows its GSTR-1 classification (see classify), so GSTR-3B, the Tax Register and the
Output GST ledgers agree month by month.
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


def month_start(d):
    return d.replace(day=1)


def is_qrmp(tenant):
    """Cenvora has no GST filing-frequency setting yet (only QRMP eligibility in gst_thresholds).
    Reads a future `gst_filing_frequency` attribute if one is added; today this is always False."""
    t = getattr(tenant, 'active_tenant', tenant)
    return (getattr(t, 'gst_filing_frequency', '') or '').lower() in ('quarterly', 'qrmp')


QRMP_WARNING = 'QRMP period handling is pending.'


def is_gstr1_filed(tenant, d):
    """Monthly flag. QRMP companies: the whole quarter is treated as unfiled (follow-up)."""
    from billing.models import GSTR1Filing
    if is_qrmp(tenant):
        return False
    return GSTR1Filing.objects.filter(created_by=tenant, period=month_start(d)).exists()


def _next_month(d):
    return (d.replace(day=28) + __import__('datetime').timedelta(days=4)).replace(day=1)


def carry_into(tenant, from_date):
    """
    Negative 3.1(a) carried into the month starting at `from_date`, per head, plus the month it
    originated in. Walks month by month from the first month with a txpd (non-netted) adjustment.
    Only full calendar months carry; returns zeros otherwise.
    """
    from billing.models import AdvanceAdjustment
    heads = ('txval', 'iamt', 'camt', 'samt')
    zero = {k: ZERO for k in heads}
    start = _d(from_date)
    if start.day != 1:
        return zero, None
    first = (AdvanceAdjustment.objects.filter(created_by=tenant, netted=False)
             .order_by('gstr1_period').values_list('gstr1_period', flat=True).first())
    if not first or first >= start:
        return zero, None
    from billing.gstr1_compute import compute_gstr1
    carry, origin = dict(zero), None
    m = month_start(first)
    while m < start:
        end = _next_month(m) - __import__('datetime').timedelta(days=1)
        raw = compute_gstr1(tenant, str(m), str(end), apply_carry=False)['table_3_1_a']
        new = {k: max(carry[k] - raw[k], ZERO) for k in heads}
        if any(v > 0 for v in new.values()):
            if not any(v > 0 for v in carry.values()):
                origin = m
        else:
            origin = None
        carry = new
        m = _next_month(m)
    return carry, (origin.strftime('%b %Y') if origin else None)


def adjustment_reference(adj_id):
    return f"Advance Adjustment {adj_id}"


def posted_advance_split(payment):
    """Gross/taxable/tax actually posted at receipt (read from the GL, not re-inferred)."""
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService
    accounts = AccountingService.get_or_create_default_accounts(payment.created_by)
    # Receipt journals plus any edit reversals/re-posts (audit trail: 'Payment Received <id> rev N' / 'v N').
    lines = list(GeneralLedgerEntry.objects.filter(reference__startswith=f"Payment Received {payment.id}",
                                                   created_by=payment.created_by))
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


def _check_open_period(tenant, on_date):
    if on_date > today_ist():
        raise ValueError('Date cannot be in the future.')
    if is_gstr1_filed(tenant, on_date):
        raise ValueError(f"GSTR-1 for {on_date.strftime('%b %Y')} is marked filed; that period is not rewritten.")


def classify(payment, invoice, recorded_on):
    """
    Rudra CA rule, on receipt date and invoice date (not the recording date):
      same period, period unfiled            -> netted (neither at nor txpd); booked in that period
      invoice period unfiled (advance earlier, already in 'at') -> txpd in the invoice period
      otherwise (period already filed with the advance in 'at') -> txpd in the recording period (catch-up)
    Returns (netted, gstr1_period_start, accounting_date).
    """
    tenant = payment.created_by
    r, i = month_start(payment.date), month_start(invoice.invoice_date)
    eff = max(payment.date, invoice.invoice_date)
    if r == i and not is_gstr1_filed(tenant, r):
        return True, r, eff
    if i > r and not is_gstr1_filed(tenant, i):
        return False, i, eff
    return False, month_start(recorded_on), recorded_on


@transaction.atomic
def apply_advance(payment, invoice, amount=None, on_date=None):
    """Apply (part of) an advance to a final invoice. Returns the AdvanceAdjustment (or None if nothing to do)."""
    from billing.models import AdvanceAdjustment, Payment
    from billing.balance_sync import recompute_customer_balance, recompute_invoice_amount_paid

    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    on_date = on_date or today_ist()
    if isinstance(on_date, str):
        on_date = date_cls.fromisoformat(on_date)
    _check_open_period(payment.created_by, on_date)
    if payment.invoice_id:
        raise ValueError('This receipt is already linked to an invoice; it is not an advance.')
    if invoice.status != 'final':
        raise ValueError('Advances can only be applied to final invoices.')
    if invoice.customer_id != payment.customer_id:
        raise ValueError('Invoice does not belong to this customer.')
    if on_date < payment.date or on_date < invoice.invoice_date:
        raise ValueError('Application date cannot be before the advance or the invoice date.')
    netted, period, acct_date = classify(payment, invoice, on_date)
    _check_open_period(payment.created_by, acct_date)

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
        payment=payment, invoice=invoice, date=acct_date, gross=amt, taxable=_q(taxable),
        cgst=_q(cgst), sgst=_q(sgst), igst=_q(igst), rate=split['rate'], created_by=payment.created_by,
        gstr1_period=period, netted=netted, recorded_on=on_date)
    _post(adj, +1, acct_date, f"Advance applied to {invoice.invoice_number}")
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
    _check_open_period(adj.created_by, on_date)
    if on_date < adj.date:
        raise ValueError('Reversal date cannot be before the application date.')
    _post(adj, -1, on_date, f"Advance un-applied from {adj.invoice.invoice_number}")
    adj.reversed_on = on_date
    adj.save(update_fields=['reversed_on'])
    recompute_invoice_amount_paid(adj.invoice_id)
    recompute_customer_balance(adj.payment.customer_id)
    return adj


# ─── GSTR-1 at / txpd (Rudra CA rule) ────────────────────────────

def _in(d, lo, hi):
    return d is not None and lo <= d <= hi


def _events(p, split):
    """(period_start, table, sign, adj) events for one service advance."""
    ev = []
    for a in p.adjustments.all():
        per = a.gstr1_period or month_start(a.date)
        ev.append((per, 'at' if a.netted else 'txpd', -1 if a.netted else 1, a))
        if a.reversed_on:
            rper = month_start(a.reversed_on)
            if rper == per:
                ev.append((per, 'at' if a.netted else 'txpd', 1 if a.netted else -1, a))  # cancels
            else:
                ev.append((rper, 'at', 1, a))  # un-applied later: advance outstanding again
    return ev


def advance_tables(tenant, from_date, to_date, seller_state_code):
    """
    GSTR-1 Table 11A ('at') and 11B ('txpd') for P = [from_date, to_date], monthly periods.
      at   = service advances received in P, minus applications netted in P (same period, unfiled),
             plus later un-applications.
      txpd = applications classified to P (advance reported in an earlier 'at').
    Classification is fixed per application when it is recorded (billing.advances.classify).
    Goods advances never appear. ad_amt is TAXABLE value.
    """
    from billing.models import AdvanceAdjustment, Payment
    from billing.gst_utils import get_pos_state_code
    lo, hi = _d(from_date), _d(to_date)
    zero = lambda: {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO}
    tots = {'at': zero(), 'txpd': zero()}
    rows = {'at': [], 'txpd': []}

    candidates = set(Payment.objects.filter(created_by=tenant, invoice__isnull=True, customer__isnull=False,
                                            date__gte=lo, date__lte=hi).values_list('id', flat=True))
    adjs = AdvanceAdjustment.objects.filter(created_by=tenant).filter(
        models_q(gstr1_period__gte=month_start(lo), gstr1_period__lte=hi) | models_q(date__gte=lo, date__lte=hi)
        | models_q(reversed_on__gte=lo, reversed_on__lte=hi))
    candidates |= set(adjs.values_list('payment_id', flat=True))

    for p in Payment.objects.filter(id__in=candidates).select_related('customer').prefetch_related('adjustments'):
        split = posted_advance_split(p)
        if split['kind'] != 'services':
            continue
        acc = {'at': {'taxable': ZERO, 'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO},
               'txpd': {'taxable': ZERO, 'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO}}
        if _in(p.date, lo, hi):
            for k in acc['at']:
                acc['at'][k] += split[k]
        for per, table, sign, a in _events(p, split):
            if month_start(lo) <= per <= hi:
                for k in acc[table]:
                    acc[table][k] += sign * getattr(a, k)
        pos_raw = getattr(p, 'place_of_supply', None) or (p.customer.state if p.customer else None)
        if str(pos_raw or '').strip().upper() == 'UNKNOWN':
            pos_raw = None
        pos = get_pos_state_code(pos_raw, fallback_state=seller_state_code) if pos_raw else seller_state_code
        if split['is_interstate'] and pos == seller_state_code:
            pos = '97'
        for table in ('at', 'txpd'):
            vals = acc[table]
            if vals['taxable'] <= 0:
                continue
            rows[table].append({'sply_ty': 'INTER' if split['is_interstate'] else 'INTRA', 'pos': pos,
                                'rt': float(split['rate']), 'ad_amt': float(vals['taxable']),
                                'camt': float(vals['cgst']), 'samt': float(vals['sgst']),
                                'iamt': float(vals['igst']), 'csamt': 0.0, 'payment_id': str(p.id)})
            tots[table]['txval'] += vals['taxable']
            tots[table]['camt'] += vals['cgst']
            tots[table]['samt'] += vals['sgst']
            tots[table]['iamt'] += vals['igst']
    return rows['at'], rows['txpd'], tots['at'], tots['txpd']


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

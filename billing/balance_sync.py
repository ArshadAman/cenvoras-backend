"""
Customer balance (UAT P1 item 2, Rudra CA).

Single source of truth for what a customer owes, shared by the customer ledger/list,
Receivables, the Udhaar dashboard and the AI assistant:

    outstanding = opening + final invoices + debit notes - receipts - credit notes

* Drafts are excluded (SalesInvoice.status == 'draft'); a receipt linked to a draft
  invoice is excluded too (the payment signals never post it).
* SalesInvoice has no 'cancelled' status and CreditNote has no status at all in this
  codebase, so there is nothing else to exclude today. Customer has no opening-balance
  field and there is no customer-side debit-note model (DebitNote is a purchase return to
  a vendor), so both terms are 0 for now; they are kept in the breakdown so the formula
  stays explicit.
* Credit notes not linked to an invoice are on-account credit: they reduce the total at
  once and are reported as `unallocated_credit` (together with unapplied advances).
"""
from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import AdvanceAdjustment, Customer, Payment, SalesInvoice

_AMOUNT_FIELD = DecimalField(max_digits=12, decimal_places=2)
_ZERO = Decimal('0.00')


def _sum_by_customer(qs, field, key='customer_id'):
    rows = qs.values(key).annotate(total=Coalesce(Sum(field), Value(0, output_field=_AMOUNT_FIELD)))
    return {row[key]: Decimal(str(row['total'] or 0)) for row in rows}


def customer_balance_breakdown(tenant, customer_ids=None):
    """
    Return {customer_id: {...components..., 'outstanding': Decimal, 'unallocated_credit': Decimal}}
    for every customer of `tenant` (or only `customer_ids`). All values Decimal.
    """
    from .models_returns import CreditNote

    customers = Customer.objects.filter(created_by=tenant)
    if customer_ids is not None:
        customers = customers.filter(id__in=list(customer_ids))
    ids = list(customers.values_list('id', flat=True))
    if not ids:
        return {}

    invoices = _sum_by_customer(
        SalesInvoice.objects.filter(created_by=tenant, customer_id__in=ids).exclude(status='draft'),
        'total_amount')
    receipts_qs = Payment.objects.filter(created_by=tenant, customer_id__in=ids).exclude(invoice__status='draft')
    receipts = _sum_by_customer(receipts_qs, 'amount')
    unlinked_receipts = _sum_by_customer(receipts_qs.filter(invoice__isnull=True), 'amount')
    applied = _sum_by_customer(
        AdvanceAdjustment.objects.filter(payment__customer_id__in=ids, payment__invoice__isnull=True,
                                         reversed_on__isnull=True),
        'gross', key='payment__customer_id')
    cn_qs = CreditNote.objects.filter(created_by=tenant, customer_id__in=ids)
    credit_notes = _sum_by_customer(cn_qs, 'total_amount')
    unlinked_cns = _sum_by_customer(cn_qs.filter(original_invoice__isnull=True), 'total_amount')

    out = {}
    for cid in ids:
        opening = _ZERO
        debit_notes = _ZERO
        inv = invoices.get(cid, _ZERO)
        rec = receipts.get(cid, _ZERO)
        cns = credit_notes.get(cid, _ZERO)
        outstanding = (opening + inv + debit_notes - rec - cns).quantize(Decimal('0.01'))
        unapplied_adv = max(unlinked_receipts.get(cid, _ZERO) - applied.get(cid, _ZERO), _ZERO)
        out[cid] = {
            'opening': opening,
            'invoices': inv,
            'debit_notes': debit_notes,
            'receipts': rec,
            'credit_notes': cns,
            'outstanding': outstanding,
            'unallocated_credit': (unapplied_adv + unlinked_cns.get(cid, _ZERO)).quantize(Decimal('0.01')),
        }
    return out


def customer_outstanding(customer):
    """Outstanding for one customer (Decimal)."""
    row = customer_balance_breakdown(customer.created_by, [customer.id]).get(customer.id)
    return row['outstanding'] if row else _ZERO


def total_receivables(tenant):
    """Sum of positive customer balances (what customers owe us), Decimal."""
    return sum((r['outstanding'] for r in customer_balance_breakdown(tenant).values() if r['outstanding'] > 0),
               _ZERO)


def recompute_invoice_amount_paid(invoice_id):
    if not invoice_id:
        return

    invoice = SalesInvoice.objects.filter(pk=invoice_id).first()
    if not invoice:
        return

    paid_total = (
        Payment.objects.filter(invoice_id=invoice_id)
        .aggregate(total=Coalesce(Sum('amount'), Value(0, output_field=_AMOUNT_FIELD)))
        .get('total')
    )

    adjusted_total = (
        AdvanceAdjustment.objects.filter(invoice_id=invoice_id, reversed_on__isnull=True)
        .aggregate(total=Coalesce(Sum('gross'), Value(0, output_field=_AMOUNT_FIELD)))
        .get('total')
    )
    paid_total = paid_total + adjusted_total

    capped_paid = min(paid_total, invoice.total_amount)
    if capped_paid < 0:
        capped_paid = 0

    SalesInvoice.objects.filter(pk=invoice_id).update(amount_paid=capped_paid)
    invoice.amount_paid = capped_paid
    invoice.refresh_payment_status(save=True)


def recompute_customer_balance(customer_id):
    if not customer_id:
        return
    customer = Customer.objects.only('id', 'created_by').filter(pk=customer_id).first()
    if not customer:
        return
    Customer.objects.filter(pk=customer_id).update(current_balance=customer_outstanding(customer))


def recompute_customer_balances_for_customers(customers, tenant):
    customers = list(customers)
    if not customers:
        return
    breakdown = customer_balance_breakdown(tenant, [c.id for c in customers])
    for customer in customers:
        row = breakdown.get(customer.id)
        if row is None:
            continue
        computed_balance = row['outstanding']
        if customer.current_balance != computed_balance:
            Customer.objects.filter(pk=customer.id).update(current_balance=computed_balance)
            customer.current_balance = computed_balance

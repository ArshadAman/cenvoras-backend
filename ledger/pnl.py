"""
Single source of truth for Cenvora's statutory Profit & Loss.

Presentation (Schedule III, Division I, as approved by Rudra CA):
    Revenue from operations              (GL revenue accounts)
    Purchases of stock-in-trade           6001 Purchases net of 6002 Purchase Return (taxable value,
                                          Input GST is in 1301/1302/1303, not here)
    Changes in inventories of stock-in-trade  = opening stock - closing stock
    Other expenses                        (other GL expense accounts)
    Net profit = GL revenue - GL expenses - (opening - closing)

There is no separate COGS expense line. A COGS estimate is returned only as a memo.

Switch: PNL_PRESENTATION = 'cogs' falls back to the old GL-only basis (no inventory change line;
5001 COGS account used if anything posts to it). Change it here and nowhere else.

Stock valuation: AT COST (weighted average, same unit cost as Reports -> Stock Valuation).
Lower of cost and NRV (AS 2) is NOT applied: Cenvora has no NRV data per item. Quantities at a
date are rolled back from the current product stock using dated documents (purchase bills,
final sales invoices, credit notes, debit notes, stock journals). Manual stock edits with no
document are treated as part of the opening position. Secondary-unit conversion is not applied.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

PNL_PRESENTATION = 'schedule_iii'  # or 'cogs'

PURCHASE_CODES = {'6001'}
PURCHASE_RETURN_CODES = {'6002'}
ZERO = Decimal('0.00')
CENT = Decimal('0.01')


def _d(v):
    return Decimal(str(v or 0))


def _parse(d):
    if d is None or isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def _movements_after(tenant, after):
    """Net qty IN per product for documents dated strictly after `after` (None = all documents)."""
    from billing.models import PurchaseBillItem, SalesInvoiceItem
    from billing.models_returns import CreditNoteItem, DebitNoteItem
    from inventory.models_sidecar import StockJournalItem

    net = defaultdict(lambda: ZERO)

    def add(qs, date_field, sign, free=True):
        if after is not None:
            qs = qs.filter(**{f'{date_field}__gt': after})
        fields = ['quantity', 'free_quantity'] if free else ['quantity']
        rows = qs.values('product_id').annotate(**{f's_{f}': Sum(f) for f in fields})
        for r in rows:
            if r['product_id'] is None:
                continue
            qty = sum((_d(r[f's_{f}']) for f in fields), ZERO)
            net[r['product_id']] += sign * qty

    add(PurchaseBillItem.objects.filter(purchase_bill__created_by=tenant), 'purchase_bill__bill_date', 1)
    add(SalesInvoiceItem.objects.filter(sales_invoice__created_by=tenant).exclude(sales_invoice__status='draft'),
        'sales_invoice__invoice_date', -1)
    add(CreditNoteItem.objects.filter(credit_note__created_by=tenant), 'credit_note__date', 1, free=False)
    add(DebitNoteItem.objects.filter(debit_note__created_by=tenant), 'debit_note__date', -1, free=False)
    add(StockJournalItem.objects.filter(journal__created_by=tenant), 'journal__date', 1, free=False)
    return net


def _unit_costs(tenant):
    from reports.services import get_stock_valuation
    costs = {}
    for row in get_stock_valuation(tenant).get('items', []):
        costs[row['id']] = _d(row['avg_cost'])
    return costs


def stock_value_as_of(tenant, as_of, costs=None):
    """
    Closing stock value at end of `as_of` (inclusive), at cost.
    as_of=None -> the position before any dated document (opening position brought into Cenvora).
    Returns (value, negative_items) where negative_items lists products with qty < 0 (data issue).
    """
    from inventory.models import Product
    costs = costs if costs is not None else _unit_costs(tenant)
    if as_of is None:
        moved = _movements_after(tenant, None)
    else:
        moved = _movements_after(tenant, as_of)
    value = ZERO
    negatives = []
    for p in Product.objects.filter(created_by=tenant, is_active=True).only('id', 'name', 'stock'):
        qty = _d(p.stock) - moved.get(p.id, ZERO)
        if qty == 0:
            continue
        line = (qty * costs.get(p.id, ZERO)).quantize(CENT)
        value += line
        if qty < 0:
            negatives.append({'product': p.name, 'qty': str(qty), 'value': str(line)})
    return value.quantize(CENT), negatives


def _gl_balances(tenant, account_type, from_date=None, to_date=None, as_of=None):
    from .models import Account, AccountType, GeneralLedgerEntry
    out = []
    for acc in Account.objects.filter(created_by=tenant, account_type=account_type).order_by('code', 'name'):
        qs = GeneralLedgerEntry.objects.filter(account=acc, created_by=tenant)
        if as_of:
            qs = qs.filter(date__lte=as_of)
        else:
            if from_date:
                qs = qs.filter(date__gte=from_date)
            if to_date:
                qs = qs.filter(date__lte=to_date)
        t = qs.aggregate(dr=Sum('debit'), cr=Sum('credit'))
        dr, cr = _d(t['dr']), _d(t['cr'])
        bal = dr - cr if account_type in (AccountType.ASSET, AccountType.EXPENSE) else cr - dr
        out.append((acc, bal))
    return out


def inventory_change(tenant, from_date=None, to_date=None, as_of=None):
    """Opening, closing and change (opening - closing) for the period. Cumulative when as_of is given."""
    if PNL_PRESENTATION != 'schedule_iii':
        return {'opening': ZERO, 'closing': ZERO, 'change': ZERO, 'negative_items': []}
    costs = _unit_costs(tenant)
    from_date, to_date, as_of = _parse(from_date), _parse(to_date), _parse(as_of)
    end = as_of or to_date or date.today()
    if as_of or not from_date:
        opening, _neg_o = stock_value_as_of(tenant, None, costs)
    else:
        opening, _neg_o = stock_value_as_of(tenant, from_date - timedelta(days=1), costs)
    closing, negatives = stock_value_as_of(tenant, end, costs)
    return {'opening': opening, 'closing': closing, 'change': (opening - closing).quantize(CENT),
            'negative_items': negatives}


def compute_net_profit(tenant, from_date=None, to_date=None, as_of=None):
    """(revenue, total_expenses incl. inventory change, net_profit, inventory dict). Decimal throughout."""
    from .models import AccountType
    rev = sum((b for _a, b in _gl_balances(tenant, AccountType.REVENUE, from_date, to_date, as_of)), ZERO)
    exp = sum((b for _a, b in _gl_balances(tenant, AccountType.EXPENSE, from_date, to_date, as_of)), ZERO)
    inv = inventory_change(tenant, from_date, to_date, as_of)
    total_exp = exp + inv['change']
    return rev, total_exp, rev - total_exp, inv


def build_statement(tenant, from_date=None, to_date=None):
    """Line items for the P&L screen. Amounts are Decimal; the view serialises them."""
    from .models import AccountType
    revenue_items, total_revenue = [], ZERO
    for acc, bal in _gl_balances(tenant, AccountType.REVENUE, from_date, to_date):
        if bal != 0 and acc.is_active:
            revenue_items.append({'id': str(acc.id), 'code': acc.code, 'name': acc.name, 'amount': bal})
        total_revenue += bal

    expense_items, total_expenses = [], ZERO
    purchases = ZERO
    purchase_ids = []
    for acc, bal in _gl_balances(tenant, AccountType.EXPENSE, from_date, to_date):
        total_expenses += bal
        if PNL_PRESENTATION == 'schedule_iii' and (acc.code in PURCHASE_CODES or acc.code in PURCHASE_RETURN_CODES):
            purchases += bal
            purchase_ids.append(str(acc.id))
            continue
        if bal != 0 and acc.is_active:
            expense_items.append({'id': str(acc.id), 'code': acc.code, 'name': acc.name, 'amount': bal})

    inv = inventory_change(tenant, from_date, to_date)
    if PNL_PRESENTATION == 'schedule_iii':
        head = [{'id': 'purchases-stock-in-trade', 'code': '6001-6002',
                 'name': 'Purchases of stock-in-trade (net of returns, excl. GST)', 'amount': purchases,
                 'accounts': purchase_ids},
                {'id': 'changes-in-inventories', 'code': 'INV',
                 'name': 'Changes in inventories of stock-in-trade (opening - closing)', 'amount': inv['change']}]
        expense_items = head + expense_items
        total_expenses += inv['change']

    net = total_revenue - total_expenses
    return {
        'basis': PNL_PRESENTATION,
        'revenue_items': revenue_items, 'total_revenue': total_revenue,
        'expense_items': expense_items, 'total_expenses': total_expenses,
        'net_profit': net, 'inventory': inv,
    }


def cogs_memo(tenant, from_date=None, to_date=None):
    """Memo/analytics only: qty sold x unit cost. Never part of the statutory P&L."""
    from billing.models import SalesInvoiceItem
    qs = SalesInvoiceItem.objects.filter(sales_invoice__created_by=tenant).exclude(sales_invoice__status='draft')
    if from_date:
        qs = qs.filter(sales_invoice__invoice_date__gte=from_date)
    if to_date:
        qs = qs.filter(sales_invoice__invoice_date__lte=to_date)
    costs = _unit_costs(tenant)
    total = ZERO
    for row in qs.values('product_id').annotate(q=Sum('quantity')):
        total += _d(row['q']) * costs.get(row['product_id'], ZERO)
    return total.quantize(CENT)

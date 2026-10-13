"""
Financial Statement Views:
- Profit & Loss Statement (Income Statement)
- Balance Sheet
- Bank Reconciliation
"""
from decimal import Decimal
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404
from billing.models import SalesInvoiceItem
from .models import Account, AccountType, GeneralLedgerEntry
from .services import AccountingService

RETAINED_EARNINGS_CODES = {'3100', 'RE', 'RETAINED_EARNINGS'}


def _account_balance(account, tenant, from_date=None, to_date=None, as_of=None):
    entries = GeneralLedgerEntry.objects.filter(account=account, created_by=tenant)
    if as_of:
        entries = entries.filter(date__lte=as_of)
    else:
        if from_date:
            entries = entries.filter(date__gte=from_date)
        if to_date:
            entries = entries.filter(date__lte=to_date)
    totals = entries.aggregate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit'),
    )
    dr = totals['total_debit'] or Decimal('0')
    cr = totals['total_credit'] or Decimal('0')
    if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
        return dr - cr
    return cr - dr


def compute_gl_net_profit(tenant, from_date=None, to_date=None, as_of=None):
    """Shared GL net profit for P&L and Balance Sheet retained earnings."""
    revenue_total = Decimal('0')
    for acc in Account.objects.filter(created_by=tenant, account_type=AccountType.REVENUE):
        revenue_total += _account_balance(acc, tenant, from_date=from_date, to_date=to_date, as_of=as_of)

    expense_total = Decimal('0')
    for acc in Account.objects.filter(created_by=tenant, account_type=AccountType.EXPENSE):
        expense_total += _account_balance(acc, tenant, from_date=from_date, to_date=to_date, as_of=as_of)

    return revenue_total, expense_total, revenue_total - expense_total



@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profit_loss_statement(request):
    """
    Profit & Loss (Income) Statement.
    Revenue - Expenses = Net Profit
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    user = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')

    # Ensure default accounts exist
    AccountingService.get_or_create_default_accounts(user)

    # Get Revenue accounts
    revenue_accounts = Account.objects.filter(
        created_by=user, account_type=AccountType.REVENUE, is_active=True
    ).order_by('code', 'name')

    # Get Expense accounts
    expense_accounts = Account.objects.filter(
        created_by=user, account_type=AccountType.EXPENSE, is_active=True
    ).order_by('code', 'name')

    def get_balance(account):
        entries = GeneralLedgerEntry.objects.filter(account=account, created_by=user)
        if from_date:
            entries = entries.filter(date__gte=from_date)
        if to_date:
            entries = entries.filter(date__lte=to_date)
        totals = entries.aggregate(
            total_debit=Sum('debit'),
            total_credit=Sum('credit')
        )
        dr = totals['total_debit'] or Decimal('0')
        cr = totals['total_credit'] or Decimal('0')
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            return dr - cr
        return cr - dr

    revenue_items = []
    total_revenue = Decimal('0')
    for acc in revenue_accounts:
        bal = get_balance(acc)
        if bal != 0:
            revenue_items.append({
                'id': str(acc.id),
                'code': acc.code,
                'name': acc.name,
                'amount': float(bal),
            })
            total_revenue += bal

    expense_items = []
    total_expenses = Decimal('0')
    for acc in expense_accounts:
        bal = get_balance(acc)
        if bal != 0:
            expense_items.append({
                'id': str(acc.id),
                'code': acc.code,
                'name': acc.name,
                'amount': float(bal),
            })
            total_expenses += bal

    sales_items = SalesInvoiceItem.objects.filter(
        Q(sales_invoice__created_by=user) | Q(sales_invoice__created_by__parent=user),
        sales_invoice__status='final',
    )
    if from_date:
        sales_items = sales_items.filter(sales_invoice__invoice_date__gte=from_date)
    if to_date:
        sales_items = sales_items.filter(sales_invoice__invoice_date__lte=to_date)

    # Synthetic COGS only when GL has no COGS — never include GST (liability)
    has_gl_cogs = any(
        (item.get('code') or '') in {'5001', 'COGS'} or 'cost of goods' in (item.get('name') or '').lower()
        for item in expense_items
    )
    sales_cogs = Decimal('0')
    if not has_gl_cogs:
        for item in sales_items.select_related('product', 'batch'):
            qty = Decimal(str(item.quantity or 0))
            if item.batch and item.batch.cost_price:
                cost_price = Decimal(str(item.batch.cost_price))
            elif item.product and item.product.price:
                cost_price = Decimal(str(item.product.price))
            else:
                cost_price = Decimal('0')
            sales_cogs += qty * cost_price

        if sales_cogs:
            expense_items.append({
                'id': 'sales-cogs',
                'code': 'COGS',
                'name': 'Cost of Goods Sold (Sales Invoices)',
                'amount': float(sales_cogs),
            })
            # Informational only — net_profit uses GL so P&L matches Balance Sheet RE

    _rev, _exp, net_profit = compute_gl_net_profit(user, from_date=from_date, to_date=to_date)
    margin = (net_profit / total_revenue * 100) if total_revenue > 0 else Decimal('0')

    return Response({
        'from_date': from_date,
        'to_date': to_date,
        'revenue': {
            'items': revenue_items,
            'total': float(total_revenue),
        },
        'expenses': {
            'items': expense_items,
            'total': float(total_expenses),
        },
        'net_profit': float(net_profit),
        'profit_margin': float(margin.quantize(Decimal('0.01'))) if isinstance(margin, Decimal) else float(margin),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def balance_sheet(request):
    """
    Balance Sheet: Assets = Liabilities + Equity
    Query Params: ?as_of=YYYY-MM-DD (defaults to today)
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    as_of = request.query_params.get('as_of')

    # Ensure default accounts exist
    AccountingService.get_or_create_default_accounts(tenant)
    # Draft invoices must not appear in balance sheet totals
    AccountingService.purge_non_final_sales_invoice_entries(tenant)

    def get_balance(account):
        entries = GeneralLedgerEntry.objects.filter(account=account, created_by=tenant)
        if as_of:
            entries = entries.filter(date__lte=as_of)
        totals = entries.aggregate(
            total_debit=Sum('debit'),
            total_credit=Sum('credit')
        )
        dr = totals['total_debit'] or Decimal('0')
        cr = totals['total_credit'] or Decimal('0')
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            return dr - cr
        return cr - dr

    # Assets
    asset_accounts = Account.objects.filter(
        created_by=tenant, account_type=AccountType.ASSET, is_active=True
    ).order_by('code', 'name')
    asset_items = []
    total_assets = Decimal('0')
    for acc in asset_accounts:
        bal = get_balance(acc)
        if bal != 0:
            asset_items.append({
                'id': str(acc.id),
                'code': acc.code,
                'name': acc.name,
                'amount': float(bal),
            })
            total_assets += bal

    # Liabilities
    liability_accounts = Account.objects.filter(
        created_by=tenant, account_type=AccountType.LIABILITY, is_active=True
    ).order_by('code', 'name')
    liability_items = []
    total_liabilities = Decimal('0')
    for acc in liability_accounts:
        bal = get_balance(acc)
        if bal != 0:
            liability_items.append({
                'id': str(acc.id),
                'code': acc.code,
                'name': acc.name,
                'amount': float(bal),
            })
            total_liabilities += bal

    # Equity (skip booked RE — computed RE added once below)
    equity_accounts = Account.objects.filter(
        created_by=tenant, account_type=AccountType.EQUITY, is_active=True
    ).order_by('code', 'name')
    equity_items = []
    total_equity = Decimal('0')
    for acc in equity_accounts:
        code = (acc.code or '').strip().upper()
        name_l = (acc.name or '').lower()
        if code in RETAINED_EARNINGS_CODES or 'retained earning' in name_l:
            continue
        bal = get_balance(acc)
        if bal != 0:
            equity_items.append({
                'id': str(acc.id),
                'code': acc.code,
                'name': acc.name,
                'amount': float(bal),
            })
            total_equity += bal

    # Retained Earnings — same helper as P&L net profit
    _rev, _exp, retained_earnings = compute_gl_net_profit(tenant, as_of=as_of)
    total_equity += retained_earnings

    is_balanced = abs(total_assets - (total_liabilities + total_equity)) < Decimal('0.01')
    difference = total_assets - (total_liabilities + total_equity)

    return Response({
        'as_of': as_of or 'current',
        'assets': {
            'items': asset_items,
            'total': float(total_assets),
        },
        'liabilities': {
            'items': liability_items,
            'total': float(total_liabilities),
        },
        'equity': {
            'items': equity_items,
            'retained_earnings': float(retained_earnings),
            'total': float(total_equity),
        },
        'is_balanced': is_balanced,
        'difference': float(difference),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def balance_sheet_account_detail(request, account_id):
    """
    Drill-down for a specific balance-sheet account.
    Query Params: ?as_of=YYYY-MM-DD
    """
    user = request.user
    as_of = request.query_params.get('as_of')

    account = get_object_or_404(
        Account,
        id=account_id,
        created_by=user,
        is_active=True,
        account_type__in=[AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY],
    )

    entries = GeneralLedgerEntry.objects.filter(account=account, created_by=user)
    if as_of:
        entries = entries.filter(date__lte=as_of)
    entries = entries.order_by('date', 'created_at')

    running_balance = Decimal('0')
    results = []
    for entry in entries:
        if account.account_type == AccountType.ASSET:
            running_balance += (entry.debit - entry.credit)
        else:
            running_balance += (entry.credit - entry.debit)

        results.append({
            'id': str(entry.id),
            'date': str(entry.date),
            'description': entry.description,
            'reference': entry.reference,
            'debit': float(entry.debit),
            'credit': float(entry.credit),
            'running_balance': float(running_balance),
        })

    totals = entries.aggregate(total_debit=Sum('debit'), total_credit=Sum('credit'))
    total_debit = totals['total_debit'] or Decimal('0')
    total_credit = totals['total_credit'] or Decimal('0')
    if account.account_type == AccountType.ASSET:
        net_balance = total_debit - total_credit
    else:
        net_balance = total_credit - total_debit

    return Response({
        'account': {
            'id': str(account.id),
            'code': account.code,
            'name': account.name,
            'account_type': account.account_type,
        },
        'as_of': as_of or 'current',
        'totals': {
            'debit': float(total_debit),
            'credit': float(total_credit),
            'net_balance': float(net_balance),
        },
        'count': len(results),
        'entries': results,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cashbook(request):
    """
    Cashbook — All cash (debit/credit) entries sorted by date.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    user = request.user
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')

    # Get cash account
    cash_account = Account.objects.filter(
        created_by=user, code='1001', account_type=AccountType.ASSET
    ).first()

    if not cash_account:
        return Response({
            'opening_balance': 0,
            'closing_balance': 0,
            'total_receipts': 0,
            'total_payments': 0,
            'entries': [],
        })

    entries = GeneralLedgerEntry.objects.filter(
        account=cash_account, created_by=user
    )
    if from_date:
        entries = entries.filter(date__gte=from_date)
    if to_date:
        entries = entries.filter(date__lte=to_date)

    entries = entries.order_by('date', 'created_at')

    # Opening balance (all entries before from_date)
    opening_balance = Decimal('0')
    if from_date:
        prior_entries = GeneralLedgerEntry.objects.filter(
            account=cash_account, created_by=user, date__lt=from_date
        ).aggregate(
            total_debit=Sum('debit'),
            total_credit=Sum('credit')
        )
        opening_balance = (prior_entries['total_debit'] or Decimal('0')) - (prior_entries['total_credit'] or Decimal('0'))

    results = []
    running_balance = opening_balance
    total_receipts = Decimal('0')
    total_payments = Decimal('0')

    for entry in entries:
        running_balance += entry.debit - entry.credit
        total_receipts += entry.debit
        total_payments += entry.credit
        results.append({
            'id': str(entry.id),
            'date': str(entry.date),
            'description': entry.description,
            'reference': entry.reference,
            'debit': float(entry.debit),
            'credit': float(entry.credit),
            'balance': float(running_balance),
        })

    return Response({
        'opening_balance': float(opening_balance),
        'closing_balance': float(running_balance),
        'total_receipts': float(total_receipts),
        'total_payments': float(total_payments),
        'count': len(results),
        'entries': results,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def balance_sheet_diagnostics(request):
    """Detailed diagnostics to trace balance-sheet and trial-balance differences."""
    tenant = getattr(request.user, 'active_tenant', request.user)
    as_of = request.query_params.get('as_of')

    trial_data = AccountingService.get_trial_balance(tenant, date_to=as_of)
    total_debits = Decimal(str(trial_data.get('total_debits', 0) or 0))
    total_credits = Decimal(str(trial_data.get('total_credits', 0) or 0))
    trial_difference = total_debits - total_credits

    sheet_data = balance_sheet(request).data
    sheet_difference = Decimal(str(sheet_data.get('difference', 0) or 0))

    suspect_accounts = []
    for account_info in trial_data.get('accounts', []):
        debit_total = Decimal(str(account_info.get('debit_total', 0) or 0))
        credit_total = Decimal(str(account_info.get('credit_total', 0) or 0))
        if debit_total != credit_total:
            suspect_accounts.append({
                'account_id': str(account_info['account'].id),
                'account_code': account_info['account'].code,
                'account_name': account_info['account'].name,
                'account_type': account_info['account'].account_type,
                'debit_total': float(debit_total),
                'credit_total': float(credit_total),
                'net': float(debit_total - credit_total),
            })

    suspect_accounts.sort(key=lambda item: abs(item['net']), reverse=True)

    suggestion = None
    if abs(sheet_difference) > Decimal('0') and abs(sheet_difference) <= Decimal('1'):
        suggestion = {
            'type': 'rounding_adjustment_review',
            'recommended_amount': float(-sheet_difference),
            'message': 'Small residual detected. Review round-off entries and decimal precision on invoices/payments.',
        }

    return Response({
        'as_of': as_of or 'current',
        'trial_balance': {
            'total_debits': float(total_debits),
            'total_credits': float(total_credits),
            'difference': float(trial_difference),
            'is_balanced': abs(trial_difference) < Decimal('0.01'),
        },
        'balance_sheet': {
            'difference': float(sheet_difference),
            'is_balanced': abs(sheet_difference) < Decimal('0.01'),
        },
        'top_accounts_by_net_movement': suspect_accounts[:20],
        'suggestion': suggestion,
    })

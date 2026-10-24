"""Record money into Bank so payroll/vendor payments see the real balance.

Sales receipts are not the only way a bank account gets funded. Owners also
deposit cash they collected, or extra capital. This posts a balanced journal:

  cash   → Dr Bank  Cr Cash
  owner  → Dr Bank  Cr Owner's Equity
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.exceptions import ValidationError

from ledger.balance_checks import get_account_balance_decimal
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService

SOURCE_CASH = 'cash'
SOURCE_OWNER = 'owner'
VALID_SOURCES = {SOURCE_CASH, SOURCE_OWNER}


def _q(value):
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('Enter a valid amount.') from exc
    if amount <= 0:
        raise ValidationError('Amount must be greater than zero.')
    return amount


def _accounts(tenant):
    return AccountingService.get_or_create_default_accounts(tenant)


def resolve_bank_cash_equity(tenant, bank_account_id=None):
    accounts = _accounts(tenant)
    bank = None
    if bank_account_id:
        from ledger.models import Account
        bank = Account.objects.filter(id=bank_account_id, created_by=tenant).first()
        if not bank:
            raise ValidationError('Bank account not found.')
        name = (bank.name or '').lower()
        if str(bank.code) == '1001' or name == 'cash' or name.startswith('cash '):
            raise ValidationError('Choose a Bank account, not Cash.')
    if bank is None:
        bank = accounts.get('1002') or accounts.get('bank') or accounts.get('bank_account')
    cash = accounts.get('1001') or accounts.get('cash')
    equity = accounts.get('3001') or accounts.get('owners_equity')
    if not bank or not cash or not equity:
        raise ValidationError('Default Cash, Bank, and Owner\'s Equity accounts are missing. Set up the chart of accounts first.')
    return bank, cash, equity


def snapshot_balances(tenant, bank, cash):
    return {
        'bank': {
            'id': str(bank.id),
            'code': bank.code,
            'name': bank.name,
            'balance': str(get_account_balance_decimal(bank, tenant).quantize(Decimal('0.01'))),
        },
        'cash': {
            'id': str(cash.id),
            'code': cash.code,
            'name': cash.name,
            'balance': str(get_account_balance_decimal(cash, tenant).quantize(Decimal('0.01'))),
        },
    }


def record_bank_deposit(tenant, *, amount, source, date, notes='', bank_account_id=None):
    source = (source or '').strip().lower()
    if source not in VALID_SOURCES:
        raise ValidationError('Source must be "cash" (move collected cash) or "owner" (extra / capital).')

    amount = _q(amount)
    bank, cash, equity = resolve_bank_cash_equity(tenant, bank_account_id)
    credit_account = cash if source == SOURCE_CASH else equity

    if source == SOURCE_CASH:
        cash_bal = get_account_balance_decimal(cash, tenant).quantize(Decimal('0.01'))
        if cash_bal < amount:
            raise ValidationError(
                f'Cash (1001) has only ₹{cash_bal}. Deposit up to that, or use Extra / owner money '
                f'if this cash was never recorded in Cenvora.'
            )

    note = (notes or '').strip()
    if source == SOURCE_CASH:
        description = 'Cash collected deposited to bank'
        reference = 'BANK-DEPOSIT-CASH'
    else:
        description = 'Owner / extra deposit to bank'
        reference = 'BANK-DEPOSIT-OWNER'
    if note:
        description = f'{description} — {note[:180]}'

    with transaction.atomic():
        GeneralLedgerEntry.objects.create(
            date=date,
            account=bank,
            debit=amount,
            credit=Decimal('0.00'),
            description=description,
            reference=reference,
            created_by=tenant,
        )
        GeneralLedgerEntry.objects.create(
            date=date,
            account=credit_account,
            debit=Decimal('0.00'),
            credit=amount,
            description=description,
            reference=reference,
            created_by=tenant,
        )

    return {
        'success': True,
        'amount': str(amount),
        'source': source,
        'description': description,
        'balances': snapshot_balances(tenant, bank, cash),
    }

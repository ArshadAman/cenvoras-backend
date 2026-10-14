"""Shared Cash/Bank balance gate for vendor payments and payroll disbursement."""
from decimal import Decimal

from rest_framework.exceptions import ValidationError


def get_account_balance_decimal(account, tenant):
    from ledger.services import AccountingService
    bal_info = AccountingService.get_account_balance(account, tenant)
    return Decimal(str(bal_info.get('balance', 0) if isinstance(bal_info, dict) else bal_info))


def assert_sufficient_balance(account, tenant, required_amount, *, allow_zero_required=False):
    """
    Raise ValidationError if balance is negative or below required_amount.
    Returns (balance, required) as Decimals on success.
    """
    required = Decimal(str(required_amount or 0)).quantize(Decimal('0.01'))
    balance = get_account_balance_decimal(account, tenant).quantize(Decimal('0.01'))
    if not allow_zero_required and required < 0:
        required = Decimal('0.00')
    if balance < 0:
        raise ValidationError(
            f"Insufficient balance in {account.name} ({account.code}). "
            f"Available ₹{balance}, required ₹{required}. Account is overdrawn."
        )
    if balance < required:
        raise ValidationError(
            f"Insufficient balance in {account.name} ({account.code}). "
            f"Available ₹{balance}, required ₹{required}."
        )
    return balance, required

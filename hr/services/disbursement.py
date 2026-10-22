"""Salary disbursement rules (Rudra CA sign-off, 22 Oct 2026).

Accrual (on approve): Dr Salaries & Wages (gross), Dr Employer contributions,
Cr Salaries Payable (net), Cr PF/ESI/TDS/PT/other payables.
Disbursement (on pay): Dr Salaries Payable, Cr the Bank account chosen by the user.
Cash is only allowed when HRMSSettings.allow_cash_salary_disbursement is on.
"""
from decimal import Decimal

S40A3_LIMIT = Decimal('10000.00')


def classify_payment_account(account):
    """Return 'bank', 'cash' or None (not a valid salary payment account)."""
    from ledger.models import AccountType
    if account is None or account.account_type != AccountType.ASSET:
        return None
    code = str(getattr(account, 'code', '') or '')
    name = (getattr(account, 'name', '') or '').strip().lower()
    if code == '1001' or name == 'cash' or name.startswith('cash '):
        return 'cash'
    if code == '1002' or 'bank' in name:
        return 'bank'
    return None


def cash_40a3_warnings(payroll_run):
    """s.40A(3): one warning per employee whose single-day cash payment exceeds ₹10,000.

    A salary run is paid on one day, so each payslip's net is that employee's payment for the day.
    """
    warnings = []
    for ps in payroll_run.payslips.select_related('employee'):
        net = Decimal(str(ps.net_salary or 0)).quantize(Decimal('0.01'))
        if net > S40A3_LIMIT:
            emp = ps.employee
            warnings.append(
                f"Section 40A(3): Cash payment of ₹{net} to {emp.full_name} ({emp.employee_code}) "
                f"exceeds ₹10,000 in a day. Prefer Bank transfer."
            )
    return warnings

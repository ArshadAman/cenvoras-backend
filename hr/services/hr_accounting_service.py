"""
HR Accounting Integration Service — Connects HRMS Payroll directly to Cenvora Double-Entry Ledger.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from ledger.models import Account, AccountType, GeneralLedgerEntry
from hr.models import PayrollRun, Payslip, EmployeeAdvanceLoan, LoanRecoveryLog
import datetime
import calendar


class HRAccountingService:
    """Handles double-entry ledger postings for Payroll lifecycle events."""

    HR_ACCOUNTS = [
        ('5300', 'Salaries & Wages Expense', AccountType.EXPENSE),
        ('5302', 'Employer Statutory Contributions', AccountType.EXPENSE),
        ('2200', 'Salaries Payable', AccountType.LIABILITY),
        ('2201', 'PF Payable', AccountType.LIABILITY),
        ('2202', 'ESI Payable', AccountType.LIABILITY),
        ('2203', 'TDS Payable', AccountType.LIABILITY),
        ('2204', 'Professional Tax Payable', AccountType.LIABILITY),
        ('1250', 'Employee Advances & Loans', AccountType.ASSET),
    ]

    @classmethod
    def get_or_create_hr_accounts(cls, tenant):
        """Ensure standard HR/payroll chart of accounts exist for this tenant."""
        accounts = {}
        for code, name, acc_type in cls.HR_ACCOUNTS:
            account, _ = Account.objects.get_or_create(
                code=code,
                created_by=tenant,
                defaults={
                    'name': name,
                    'account_type': acc_type,
                    'description': f"HRMS {name} account",
                    'is_active': True,
                }
            )
            accounts[code] = account
        return accounts

    @classmethod
    @transaction.atomic
    def post_payroll_accrual(cls, payroll_run, user):
        """
        Posts accrual journal entries when payroll is APPROVED:
          Dr. Salaries & Wages Expense (Total Gross)
          Dr. Employer Statutory Contributions (Total Employer PF + ESI)
          Cr. Salaries Payable (Total Net Take-Home)
          Cr. PF Payable (Employee PF + Employer PF)
          Cr. ESI Payable (Employee ESI + Employer ESI)
          Cr. TDS Payable (Total TDS)
          Cr. Professional Tax Payable (Total PT)
          Cr. Employee Advances & Loans (Total Recovered Advances/Loans)
        """
        tenant = payroll_run.tenant
        accounts = cls.get_or_create_hr_accounts(tenant)
        ref = f"PAYROLL-ACCRUAL-{payroll_run.year}-{payroll_run.month:02d}"

        # Delete any existing accrual entries for this run reference to ensure idempotency
        GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).delete()

        payslips = Payslip.objects.filter(payroll_run=payroll_run)
        if not payslips.exists():
            return

        total_gross = sum(p.gross_salary for p in payslips)
        total_net = sum(p.net_salary for p in payslips)
        total_emp_pf = sum(p.employee_pf for p in payslips)
        total_empr_pf = sum(p.employer_pf for p in payslips)
        total_emp_esi = sum(p.employee_esi for p in payslips)
        total_empr_esi = sum(p.employer_esi for p in payslips)
        total_tds = sum(p.tds for p in payslips)
        total_pt = sum(p.professional_tax for p in payslips)
        total_advances = sum(p.advance_recovery + p.loan_recovery for p in payslips)
        total_empr_contributions = sum(p.employer_total_contribution for p in payslips)

        period_label = f"{calendar.month_name[payroll_run.month]} {payroll_run.year}"
        entry_date = datetime.date(payroll_run.year, payroll_run.month, calendar.monthrange(payroll_run.year, payroll_run.month)[1])

        # 1. Dr. Salaries & Wages Expense (Gross)
        if total_gross > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['5300'],
                debit=total_gross,
                credit=Decimal('0.00'),
                description=f"Gross Salaries & Wages for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 2. Dr. Employer Statutory Contributions Expense
        if total_empr_contributions > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['5302'],
                debit=total_empr_contributions,
                credit=Decimal('0.00'),
                description=f"Employer PF & ESI contributions for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 3. Cr. Salaries Payable (Net take-home)
        if total_net > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['2200'],
                debit=Decimal('0.00'),
                credit=total_net,
                description=f"Net Salaries Payable for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 4. Cr. PF Payable (Employee + Employer)
        combined_pf = total_emp_pf + total_empr_pf
        if combined_pf > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['2201'],
                debit=Decimal('0.00'),
                credit=combined_pf,
                description=f"PF Payable (Employee ₹{total_emp_pf} + Employer ₹{total_empr_pf}) for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 5. Cr. ESI Payable (Employee + Employer)
        combined_esi = total_emp_esi + total_empr_esi
        if combined_esi > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['2202'],
                debit=Decimal('0.00'),
                credit=combined_esi,
                description=f"ESI Payable (Employee ₹{total_emp_esi} + Employer ₹{total_empr_esi}) for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 6. Cr. TDS Payable
        if total_tds > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['2203'],
                debit=Decimal('0.00'),
                credit=total_tds,
                description=f"TDS Payable deducted for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 7. Cr. Professional Tax Payable
        if total_pt > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['2204'],
                debit=Decimal('0.00'),
                credit=total_pt,
                description=f"Professional Tax Payable for {period_label}",
                reference=ref,
                created_by=tenant,
            )

        # 8. Cr. Employee Advances & Loans Recovered
        if total_advances > 0:
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=accounts['1250'],
                debit=Decimal('0.00'),
                credit=total_advances,
                description=f"Employee Advances & Loans recovered in {period_label}",
                reference=ref,
                created_by=tenant,
            )

    @classmethod
    @transaction.atomic
    def post_payroll_disbursement(cls, payroll_run, payment_account, user):
        """
        Posts disbursement entries when salary is PAID:
          Dr. Salaries Payable (Total Net Salary)
          Cr. Selected Bank/Cash Account (Total Net Salary)
        And applies advance/loan balance deductions with LoanRecoveryLog records.
        """
        tenant = payroll_run.tenant
        accounts = cls.get_or_create_hr_accounts(tenant)
        ref = f"PAYROLL-PAID-{payroll_run.year}-{payroll_run.month:02d}"

        # Delete any existing payment entries for this run reference
        GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).delete()

        payslips = Payslip.objects.filter(payroll_run=payroll_run)
        total_net = sum(p.net_salary for p in payslips)
        period_label = f"{calendar.month_name[payroll_run.month]} {payroll_run.year}"
        payment_date = payroll_run.paid_at.date() if payroll_run.paid_at else timezone.now().date()

        # If payment_account is not specified, default to Cash (1001)
        if not payment_account:
            from ledger.services import AccountingService
            default_accs = AccountingService.get_or_create_default_accounts(tenant)
            payment_account = default_accs.get('1001') or accounts['2200']

        if total_net > 0:
            # 1. Dr. Salaries Payable
            GeneralLedgerEntry.objects.create(
                date=payment_date,
                account=accounts['2200'],
                debit=total_net,
                credit=Decimal('0.00'),
                description=f"Disbursement of Net Salaries for {period_label}",
                reference=ref,
                created_by=tenant,
            )

            # 2. Cr. Bank Account / Cash
            GeneralLedgerEntry.objects.create(
                date=payment_date,
                account=payment_account,
                debit=Decimal('0.00'),
                credit=total_net,
                description=f"Salary disbursement for {period_label} from {payment_account.name}",
                reference=ref,
                created_by=tenant,
            )

        # Process loan recovery balance deductions & logging
        for ps in payslips:
            recovery_amt = ps.advance_recovery + ps.loan_recovery
            if recovery_amt > 0:
                active_loans = EmployeeAdvanceLoan.objects.filter(
                    employee=ps.employee,
                    status='active',
                    outstanding_balance__gt=0
                ).order_by('disbursement_date')

                remaining_to_deduct = recovery_amt
                for loan in active_loans:
                    if remaining_to_deduct <= 0:
                        break
                    deduct = min(remaining_to_deduct, loan.outstanding_balance)
                    loan.outstanding_balance -= deduct
                    if loan.outstanding_balance == 0:
                        loan.status = 'fully_recovered'
                    loan.save(update_fields=['outstanding_balance', 'status'])

                    LoanRecoveryLog.objects.create(
                        tenant=tenant,
                        loan=loan,
                        payslip=ps,
                        recovery_date=payment_date,
                        amount_recovered=deduct,
                        balance_after=loan.outstanding_balance,
                    )
                    remaining_to_deduct -= deduct

    @classmethod
    @transaction.atomic
    def reverse_payroll_accrual(cls, payroll_run, user, reason):
        """
        Reverses accounting entries when a locked or approved payroll run is reopened.
        """
        tenant = payroll_run.tenant
        accrual_ref = f"PAYROLL-ACCRUAL-{payroll_run.year}-{payroll_run.month:02d}"
        payment_ref = f"PAYROLL-PAID-{payroll_run.year}-{payroll_run.month:02d}"

        # Remove both accrual and payment ledger entries
        GeneralLedgerEntry.objects.filter(created_by=tenant, reference__in=[accrual_ref, payment_ref]).delete()
        LoanRecoveryLog.objects.filter(payslip__payroll_run=payroll_run).delete()

"""
Correct a payroll disbursement that was wrongly credited to Cash (1001).

Built for QA finding P0-4c (September 2026, ref PAYROLL-PAID-2026-09, Cr ₹2,77,608.00 to
Cash 1001 on 22/09/2026). Signed-off approach (Rudra CA, 22 Oct 2026):

  1. Reversal  ref PAYROLL-PAID-2026-09-REV, SAME DATE as the original:
     mirrors every leg of the original (its Dr Salaries Payable becomes Cr, its Cr Cash becomes Dr).
  2. Re-post   ref PAYROLL-PAID-2026-09-R1, dated --bank-date (REQUIRED, no default):
     Dr Salaries Payable (2250) / Cr Bank (1002).

The original entries are never edited or deleted. DRY-RUN BY DEFAULT: nothing is written
unless --apply is passed. Idempotent: if -REV or -R1 already exist the command refuses to post.

--check-only lists every payroll posting touching Cash 1001 (read-only) plus the Cash
balance, to explain why Cash is -2,67,408.01 when the September entry is 2,77,608.

MUST NOT be run against production without explicit sign-off. --apply additionally requires
the environment variable CENVORA_ALLOW_LEDGER_CORRECTION=yes.

Examples:
  python manage.py correct_payroll_cash_posting --tenant <username|email> --year 2026 --month 9 --check-only
  python manage.py correct_payroll_cash_posting --tenant <username|email> --year 2026 --month 9 --bank-date 2026-10-25
  CENVORA_ALLOW_LEDGER_CORRECTION=yes python manage.py correct_payroll_cash_posting ... --bank-date 2026-10-25 --apply
"""
import datetime
import os
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Sum

ZERO = Decimal('0.00')


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CommandError(f"Invalid date {value!r}; use YYYY-MM-DD.")


class Command(BaseCommand):
    help = 'Dry-run-by-default correction for a payroll disbursement posted to Cash 1001 (reverse + re-post to Bank 1002).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', required=True, help='Tenant username, email or id.')
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--bank-date', dest='bank_date', help='REQUIRED for correction: date of the bank payment (YYYY-MM-DD). No default.')
        parser.add_argument('--check-only', action='store_true', help='Read-only: list payroll postings touching Cash 1001.')
        parser.add_argument('--apply', action='store_true', help='Actually write entries (default is dry-run).')

    # ── helpers ───────────────────────────────────────────────────────────
    def _tenant(self, ident):
        User = get_user_model()
        q = Q(username=ident) | Q(email__iexact=ident)
        if str(ident).isdigit():
            q |= Q(pk=int(ident))
        users = list(User.objects.filter(q)[:2])
        if len(users) != 1:
            raise CommandError(f"Tenant {ident!r} not found or ambiguous.")
        return users[0]

    def _fmt(self, d):
        return f"{Decimal(d):,.2f}"

    def check_cash(self, tenant):
        from ledger.models import GeneralLedgerEntry
        cash_q = GeneralLedgerEntry.objects.filter(created_by=tenant, account__code='1001')
        payroll = cash_q.filter(reference__startswith='PAYROLL').order_by('date', 'created_at')
        self.stdout.write('Payroll postings touching Cash 1001 (read-only):')
        for e in payroll:
            self.stdout.write(f"  {e.date}  {e.reference:<28} Dr {self._fmt(e.debit):>14}  Cr {self._fmt(e.credit):>14}  {e.description}")
        p = payroll.aggregate(d=Sum('debit'), c=Sum('credit'))
        a = cash_q.aggregate(d=Sum('debit'), c=Sum('credit'))
        p_net = (p['d'] or ZERO) - (p['c'] or ZERO)
        a_net = (a['d'] or ZERO) - (a['c'] or ZERO)
        self.stdout.write(f"Payroll net on Cash:      {self._fmt(p_net)}")
        self.stdout.write(f"Non-payroll net on Cash:  {self._fmt(a_net - p_net)}")
        self.stdout.write(f"Cash 1001 balance (Dr-Cr): {self._fmt(a_net)}")
        others = cash_q.exclude(reference__startswith='PAYROLL').values('reference').annotate(
            d=Sum('debit'), c=Sum('credit')).order_by('reference')
        self.stdout.write('Non-payroll Cash activity by reference:')
        for o in others:
            self.stdout.write(f"  {o['reference'] or '(blank)':<28} Dr {self._fmt(o['d'] or ZERO):>14}  Cr {self._fmt(o['c'] or ZERO):>14}")
        return a_net

    # ── main ──────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        from ledger.models import GeneralLedgerEntry
        from ledger.services import AccountingService
        from hr.services.hr_accounting_service import HRAccountingService

        tenant = self._tenant(opts['tenant'])
        year, month = opts['year'], opts['month']
        ref = f"PAYROLL-PAID-{year}-{month:02d}"
        rev_ref, r1_ref = f"{ref}-REV", f"{ref}-R1"

        self.check_cash(tenant)
        if opts['check_only']:
            return

        if not opts.get('bank_date'):
            raise CommandError('--bank-date is required (no default). Pass the actual bank payment date.')
        bank_date = _parse_date(opts['bank_date'])

        original = list(GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).select_related('account'))
        if not original:
            raise CommandError(f"No entries with reference {ref} for this tenant.")
        cash_legs = [e for e in original if e.account.code == '1001' and e.credit > 0]
        if not cash_legs:
            raise CommandError(f"{ref} has no credit to Cash 1001; nothing to correct.")
        dr_total = sum((e.debit for e in original), ZERO)
        cr_total = sum((e.credit for e in original), ZERO)
        if dr_total != cr_total:
            raise CommandError(f"{ref} is unbalanced (Dr {dr_total} vs Cr {cr_total}); refusing.")
        if GeneralLedgerEntry.objects.filter(created_by=tenant, reference__in=[rev_ref, r1_ref]).exists():
            self.stdout.write(self.style.WARNING(f"{rev_ref}/{r1_ref} already exist; correction already applied. Nothing to do."))
            return

        amount = sum((e.credit for e in cash_legs), ZERO)
        hr_accounts = HRAccountingService.get_or_create_hr_accounts(tenant) if opts['apply'] else None
        bank = AccountingService.get_or_create_default_accounts(tenant).get('1002') if opts['apply'] else None

        plan = []
        for e in original:  # exact mirror, same date as the original
            plan.append(dict(date=e.date, account=e.account, debit=e.credit, credit=e.debit,
                             description=f"Reversal of {ref}: {e.description}", reference=rev_ref))
        plan.append(dict(date=bank_date, account=hr_accounts['2250'] if hr_accounts else None, code='2250',
                         debit=amount, credit=ZERO,
                         description=f"Re-post of {ref}: salary disbursement via Bank", reference=r1_ref))
        plan.append(dict(date=bank_date, account=bank, code='1002', debit=ZERO, credit=amount,
                         description=f"Re-post of {ref}: salary disbursement via Bank", reference=r1_ref))

        self.stdout.write(f"\nPlanned entries ({'APPLY' if opts['apply'] else 'DRY-RUN'}):")
        for p in plan:
            code = p['account'].code if p.get('account') is not None else p.get('code')
            self.stdout.write(f"  {p['date']}  {p['reference']:<28} {code}  Dr {self._fmt(p['debit']):>14}  Cr {self._fmt(p['credit']):>14}")

        if not opts['apply']:
            self.stdout.write(self.style.WARNING('Dry-run only. Nothing written. Pass --apply to post.'))
            return
        if os.environ.get('CENVORA_ALLOW_LEDGER_CORRECTION', '').lower() != 'yes':
            raise CommandError('Refusing to --apply without CENVORA_ALLOW_LEDGER_CORRECTION=yes.')
        if bank is None:
            raise CommandError('Bank account 1002 not found.')

        with transaction.atomic():
            for p in plan:
                GeneralLedgerEntry.objects.create(
                    date=p['date'], account=p['account'], debit=p['debit'], credit=p['credit'],
                    description=p['description'], reference=p['reference'], created_by=tenant,
                )
        self.stdout.write(self.style.SUCCESS(f"Posted {rev_ref} and {r1_ref} (₹{self._fmt(amount)})."))
        self.check_cash(tenant)

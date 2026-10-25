"""
Correct a payroll disbursement that was wrongly credited to Cash (1001).

Built for QA finding P0-4c (September 2026, ref PAYROLL-PAID-2026-09, Cr ₹2,77,608.00 to
Cash 1001 on 22/09/2026). Signed-off approach (Rudra CA, 22 Oct 2026):

  1. Reversal  ref PAYROLL-PAID-2026-09-REV, SAME DATE as the original:
     mirrors every leg of the original (its Dr Salaries Payable becomes Cr, its Cr Cash becomes Dr).
  2. Re-post   ref PAYROLL-PAID-2026-09-R1, dated --bank-date (or the original pay date):
     Dr Salaries Payable (2250) / Cr Bank (1002).

The original entries are never edited or deleted. DRY-RUN BY DEFAULT: nothing is written
unless --apply is passed. Idempotent: if -REV or -R1 already exist the command refuses to post.

Omit --tenant to scan every account. Omit --year/--month to correct every PAYROLL-PAID
period that credited Cash 1001.

MUST NOT be run against production without explicit sign-off. --apply additionally requires
the environment variable CENVORA_ALLOW_LEDGER_CORRECTION=yes.

Examples:
  python manage.py correct_payroll_cash_posting --check-only
  python manage.py correct_payroll_cash_posting --tenant someuser --year 2026 --month 9 --check-only
  CENVORA_ALLOW_LEDGER_CORRECTION=yes python manage.py correct_payroll_cash_posting --apply
"""
import datetime
import os
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q, Sum

ZERO = Decimal('0.00')
PAID_REF = re.compile(r'^PAYROLL-PAID-(\d{4})-(\d{2})$')


def _parse_date(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CommandError(f"Invalid date {value!r}; use YYYY-MM-DD.")


class Command(BaseCommand):
    help = 'Dry-run-by-default correction for payroll disbursements posted to Cash 1001 (reverse + re-post to Bank 1002).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Tenant username, email or id. Omit to scan every account.')
        parser.add_argument('--year', type=int, help='Payroll year. Omit to correct every Cash-posted period.')
        parser.add_argument('--month', type=int, help='Payroll month 1-12. Omit to correct every Cash-posted period.')
        parser.add_argument('--bank-date', dest='bank_date', help='Date of the bank re-post (YYYY-MM-DD). Defaults to the original pay date when scanning all accounts.')
        parser.add_argument('--check-only', action='store_true', help='Read-only: list payroll postings touching Cash 1001.')
        parser.add_argument('--apply', action='store_true', help='Actually write entries (default is dry-run).')

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
        self.stdout.write(f'Payroll postings touching Cash 1001 for {tenant.username} (read-only):')
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

    def _jobs(self, opts):
        from ledger.models import GeneralLedgerEntry

        qs = GeneralLedgerEntry.objects.filter(
            account__code='1001',
            credit__gt=0,
            reference__regex=r'^PAYROLL-PAID-\d{4}-\d{2}$',
        )
        explicit = bool(opts.get('tenant') and opts.get('year') and opts.get('month'))
        if opts.get('tenant'):
            tenant = self._tenant(opts['tenant'])
            qs = qs.filter(created_by=tenant)
        if opts.get('year') and opts.get('month'):
            qs = qs.filter(reference=f"PAYROLL-PAID-{opts['year']}-{opts['month']:02d}")
        elif opts.get('year') or opts.get('month'):
            raise CommandError('Pass both --year and --month, or omit both to scan every period.')

        rows = list(qs.values_list('created_by_id', 'reference').distinct())
        if explicit and not rows:
            raise CommandError(
                f"No Cash 1001 credit for PAYROLL-PAID-{opts['year']}-{opts['month']:02d} "
                f"for this tenant."
            )
        User = get_user_model()
        users = {str(u.pk): u for u in User.objects.filter(pk__in=[r[0] for r in rows])}
        jobs = []
        for uid, ref in rows:
            match = PAID_REF.match(ref)
            if not match:
                continue
            tenant = users.get(str(uid))
            if tenant is None:
                continue
            jobs.append((tenant, int(match.group(1)), int(match.group(2))))
        return jobs, explicit

    def _correct_one(self, tenant, year, month, bank_date, apply):
        from ledger.models import GeneralLedgerEntry
        from ledger.services import AccountingService
        from hr.services.hr_accounting_service import HRAccountingService

        ref = f"PAYROLL-PAID-{year}-{month:02d}"
        rev_ref, r1_ref = f"{ref}-REV", f"{ref}-R1"
        original = list(GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).select_related('account'))
        if not original:
            self.stdout.write(self.style.WARNING(f"  {tenant.username} {ref}: no entries; skip."))
            return False
        cash_legs = [e for e in original if e.account.code == '1001' and e.credit > 0]
        if not cash_legs:
            self.stdout.write(self.style.WARNING(f"  {tenant.username} {ref}: no Cash 1001 credit; skip."))
            return False
        dr_total = sum((e.debit for e in original), ZERO)
        cr_total = sum((e.credit for e in original), ZERO)
        if dr_total != cr_total:
            raise CommandError(f"{tenant.username} {ref} is unbalanced (Dr {dr_total} vs Cr {cr_total}); refusing.")
        if GeneralLedgerEntry.objects.filter(created_by=tenant, reference__in=[rev_ref, r1_ref]).exists():
            self.stdout.write(self.style.WARNING(f"  {tenant.username} {rev_ref}/{r1_ref} already exist; skip."))
            return False

        amount = sum((e.credit for e in cash_legs), ZERO)
        if bank_date is None:
            bank_date = cash_legs[0].date
        hr_accounts = HRAccountingService.get_or_create_hr_accounts(tenant) if apply else None
        bank = AccountingService.get_or_create_default_accounts(tenant).get('1002') if apply else None

        plan = []
        for e in original:
            plan.append(dict(date=e.date, account=e.account, debit=e.credit, credit=e.debit,
                             description=f"Reversal of {ref}: {e.description}", reference=rev_ref))
        plan.append(dict(date=bank_date, account=hr_accounts['2250'] if hr_accounts else None, code='2250',
                         debit=amount, credit=ZERO,
                         description=f"Re-post of {ref}: salary disbursement via Bank", reference=r1_ref))
        plan.append(dict(date=bank_date, account=bank, code='1002', debit=ZERO, credit=amount,
                         description=f"Re-post of {ref}: salary disbursement via Bank", reference=r1_ref))

        self.stdout.write(f"\n{tenant.username} {ref} ({'APPLY' if apply else 'DRY-RUN'}):")
        for p in plan:
            code = p['account'].code if p.get('account') is not None else p.get('code')
            self.stdout.write(f"  {p['date']}  {p['reference']:<28} {code}  Dr {self._fmt(p['debit']):>14}  Cr {self._fmt(p['credit']):>14}")

        if not apply:
            return True
        if bank is None:
            raise CommandError(f'{tenant.username}: Bank account 1002 not found.')
        with transaction.atomic():
            for p in plan:
                GeneralLedgerEntry.objects.create(
                    date=p['date'], account=p['account'], debit=p['debit'], credit=p['credit'],
                    description=p['description'], reference=p['reference'], created_by=tenant,
                )
        self.stdout.write(self.style.SUCCESS(f"  Posted {rev_ref} and {r1_ref} (₹{self._fmt(amount)})."))
        return True

    def handle(self, *args, **opts):
        jobs, explicit = self._jobs(opts)
        tenants = []
        seen = set()
        for tenant, _year, _month in jobs:
            if tenant.pk not in seen:
                seen.add(tenant.pk)
                tenants.append(tenant)
        if opts.get('tenant') and not tenants:
            tenants = [self._tenant(opts['tenant'])]
        for tenant in tenants:
            self.check_cash(tenant)
        if opts['check_only']:
            return

        bank_date = _parse_date(opts['bank_date']) if opts.get('bank_date') else None
        if explicit and bank_date is None:
            raise CommandError('--bank-date is required (no default). Pass the actual bank payment date.')

        if not jobs:
            self.stdout.write(self.style.WARNING('No payroll Cash 1001 credits to correct.'))
            return

        if opts['apply'] and os.environ.get('CENVORA_ALLOW_LEDGER_CORRECTION', '').lower() != 'yes':
            raise CommandError('Refusing to --apply without CENVORA_ALLOW_LEDGER_CORRECTION=yes.')

        posted = 0
        for tenant, year, month in jobs:
            if self._correct_one(tenant, year, month, bank_date, opts['apply']):
                posted += 1

        if not opts['apply']:
            self.stdout.write(self.style.WARNING(
                f'Dry-run only. {posted} payroll run(s) would be corrected. Pass --apply to post.'
            ))
            return
        self.stdout.write(self.style.SUCCESS(f'Corrected {posted} payroll run(s).'))
        for tenant in tenants:
            self.check_cash(tenant)

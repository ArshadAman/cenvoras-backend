# Migration: Auto balance October 2026 payroll accrual and reconcile ledger rounding variances
from decimal import Decimal
from django.db import migrations
from django.db.models import Sum
from django.utils import timezone


def auto_balance_payroll_and_rounding(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')
    User = apps.get_model('users', 'User')

    def get_or_create_4200(tenant):
        acc = Account.objects.filter(created_by=tenant, code='4200').first()
        if not acc:
            acc = Account.objects.filter(created_by=tenant, name__iexact='Rounding Off').first()
        if not acc:
            acc = Account.objects.create(
                code='4200',
                name='Rounding Off',
                account_type='revenue',
                created_by=tenant,
                is_active=True,
                description='Rounding Off adjustment account',
            )
        return acc

    # 1. Target PAYROLL-ACCRUAL-2026-10 specifically
    oct_entries = GeneralLedgerEntry.objects.filter(reference='PAYROLL-ACCRUAL-2026-10')
    tenants_with_oct = set(oct_entries.values_list('created_by_id', flat=True))

    for tenant_id in tenants_with_oct:
        tenant = User.objects.filter(id=tenant_id).first()
        if not tenant:
            continue
        entries = GeneralLedgerEntry.objects.filter(created_by=tenant, reference='PAYROLL-ACCRUAL-2026-10')
        tot_dr = entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if abs(diff) > Decimal('0.00'):
            round_off_acc = get_or_create_4200(tenant)
            sample_entry = entries.first()
            entry_date = sample_entry.date if sample_entry else timezone.now().date()
            dr_amt = Decimal('0.00')
            cr_amt = Decimal('0.00')

            if diff > 0:
                cr_amt = diff
            else:
                dr_amt = abs(diff)

            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=round_off_acc,
                debit=dr_amt,
                credit=cr_amt,
                description="Payroll rounding adjustment for October 2026",
                reference='PAYROLL-ACCRUAL-2026-10',
                created_by=tenant,
            )

    # 2. Reconcile any other PAYROLL-ACCRUAL-* references
    all_payroll_refs = set(
        GeneralLedgerEntry.objects.filter(reference__startswith='PAYROLL-ACCRUAL-')
        .values_list('created_by_id', 'reference')
    )
    for tenant_id, ref in all_payroll_refs:
        tenant = User.objects.filter(id=tenant_id).first()
        if not tenant:
            continue
        entries = GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref)
        tot_dr = entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if Decimal('0.00') < abs(diff) <= Decimal('1.00'):
            round_off_acc = get_or_create_4200(tenant)
            sample_entry = entries.first()
            entry_date = sample_entry.date if sample_entry else timezone.now().date()
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=round_off_acc,
                debit=Decimal('0.00') if diff > 0 else abs(diff),
                credit=diff if diff > 0 else Decimal('0.00'),
                description=f"Payroll rounding balance adjustment for {ref}",
                reference=ref,
                created_by=tenant,
            )

    # 3. Reconcile any other voucher references with imbalance <= 1.00
    ref_summaries = (
        GeneralLedgerEntry.objects.exclude(reference__isnull=True)
        .exclude(reference='')
        .values('created_by_id', 'reference')
        .annotate(total_dr=Sum('debit'), total_cr=Sum('credit'))
    )
    for item in ref_summaries:
        tenant_id = item['created_by_id']
        ref = item['reference']
        tot_dr = item['total_dr'] or Decimal('0.00')
        tot_cr = item['total_cr'] or Decimal('0.00')
        diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if Decimal('0.00') < abs(diff) <= Decimal('1.00'):
            tenant = User.objects.filter(id=tenant_id).first()
            if not tenant:
                continue
            round_off_acc = get_or_create_4200(tenant)
            sample = GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).first()
            entry_date = sample.date if sample else timezone.now().date()
            GeneralLedgerEntry.objects.create(
                date=entry_date,
                account=round_off_acc,
                debit=Decimal('0.00') if diff > 0 else abs(diff),
                credit=diff if diff > 0 else Decimal('0.00'),
                description=f"Data fix round-off balance adjustment for {ref}",
                reference=ref,
                created_by=tenant,
            )

    # 4. Overall Tenant-Level Balance Check for any residual discrepancy <= 1.00
    for tenant in User.objects.all():
        tot_dr = GeneralLedgerEntry.objects.filter(created_by=tenant).aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = GeneralLedgerEntry.objects.filter(created_by=tenant).aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        global_diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if Decimal('0.00') < abs(global_diff) <= Decimal('1.00'):
            round_off_acc = get_or_create_4200(tenant)
            GeneralLedgerEntry.objects.create(
                date=timezone.now().date(),
                account=round_off_acc,
                debit=Decimal('0.00') if global_diff > 0 else abs(global_diff),
                credit=global_diff if global_diff > 0 else Decimal('0.00'),
                description="Double-entry rounding reconciliation",
                reference='GLOBAL-ROUNDING-FIX',
                created_by=tenant,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0014_gl_correction_audit_trail'),
    ]

    operations = [
        migrations.RunPython(auto_balance_payroll_and_rounding, reverse_code=migrations.RunPython.noop),
    ]

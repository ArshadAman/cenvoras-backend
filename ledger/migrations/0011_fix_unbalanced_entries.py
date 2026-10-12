# Migration: Fix unbalanced ledger entries and post round-off differences to account 4200
# CA AUDIT (Issue 3): Idempotent data fix that finds existing unbalanced entries, corrects them,
# posts difference to account 4200 Round Off, removes auto-created Suspense entries, and logs what it changed.

from decimal import Decimal
from django.db import migrations
from django.db.models import Sum


def fix_unbalanced_entries(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')

    try:
        AuditLog = apps.get_model('audit_log', 'AuditLog')
    except Exception:
        AuditLog = None

    try:
        ActionLog = apps.get_model('users', 'ActionLog')
    except Exception:
        ActionLog = None

    # 1. Remove any auto-created virtual Suspense accounts/entries
    suspense_accounts = Account.objects.filter(code__in=['9999', '9998']) | Account.objects.filter(name__iexact='Suspense Account')
    for s_acc in suspense_accounts:
        GeneralLedgerEntry.objects.filter(account=s_acc).delete()
        s_acc.delete()

    # 2. Collect all distinct voucher scopes
    # Sales Invoices
    inv_ids = list(GeneralLedgerEntry.objects.filter(sales_invoice__isnull=False).values_list('sales_invoice_id', flat=True).distinct())
    # Purchase Bills
    bill_ids = list(GeneralLedgerEntry.objects.filter(purchase_bill__isnull=False).values_list('purchase_bill_id', flat=True).distinct())
    # Credit Notes
    cn_ids = list(GeneralLedgerEntry.objects.filter(credit_note__isnull=False).values_list('credit_note_id', flat=True).distinct())
    # Debit Notes
    dn_ids = list(GeneralLedgerEntry.objects.filter(debit_note__isnull=False).values_list('debit_note_id', flat=True).distinct())

    repaired_vouchers = []

    def balance_entry_group(entries, scope_name, ref_id_or_str, user):
        tot_dr = entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if abs(diff) > Decimal('0.001'):
            # Get or create 4200 Round Off account
            round_off_acc, _ = Account.objects.get_or_create(
                code='4200',
                created_by=user,
                defaults={
                    'name': 'Rounding Off',
                    'account_type': 'revenue',
                    'description': 'Rounding Off Adjustment'
                }
            )

            sample_entry = entries.first()
            date = sample_entry.date if sample_entry else None
            from django.utils import timezone
            if not date:
                date = timezone.now().date()

            dr_amt = Decimal('0.00')
            cr_amt = Decimal('0.00')

            if diff > 0:
                # Debits exceed Credits: add Credit to Round Off
                cr_amt = diff
            else:
                # Credits exceed Debits: add Debit to Round Off
                dr_amt = abs(diff)

            kwargs = {
                'date': date,
                'account': round_off_acc,
                'debit': dr_amt,
                'credit': cr_amt,
                'description': f"Data fix round-off balance adjustment for {scope_name} {ref_id_or_str}",
                'reference': str(ref_id_or_str),
                'created_by': user,
            }
            if scope_name == 'SalesInvoice':
                kwargs['sales_invoice_id'] = ref_id_or_str
            elif scope_name == 'PurchaseBill':
                kwargs['purchase_bill_id'] = ref_id_or_str
            elif scope_name == 'CreditNote':
                kwargs['credit_note_id'] = ref_id_or_str
            elif scope_name == 'DebitNote':
                kwargs['debit_note_id'] = ref_id_or_str

            GeneralLedgerEntry.objects.create(**kwargs)
            repaired_vouchers.append(f"{scope_name} {ref_id_or_str} (Dr ₹{tot_dr} vs Cr ₹{tot_cr}, diff ₹{diff})")

    # Check Sales Invoices
    for inv_id in inv_ids:
        entries = GeneralLedgerEntry.objects.filter(sales_invoice_id=inv_id)
        if entries.exists():
            balance_entry_group(entries, 'SalesInvoice', inv_id, entries.first().created_by)

    # Check Purchase Bills
    for bill_id in bill_ids:
        entries = GeneralLedgerEntry.objects.filter(purchase_bill_id=bill_id)
        if entries.exists():
            balance_entry_group(entries, 'PurchaseBill', bill_id, entries.first().created_by)

    # Check Credit Notes
    for cn_id in cn_ids:
        entries = GeneralLedgerEntry.objects.filter(credit_note_id=cn_id)
        if entries.exists():
            balance_entry_group(entries, 'CreditNote', cn_id, entries.first().created_by)

    # Check Debit Notes
    for dn_id in dn_ids:
        entries = GeneralLedgerEntry.objects.filter(debit_note_id=dn_id)
        if entries.exists():
            balance_entry_group(entries, 'DebitNote', dn_id, entries.first().created_by)

    # Also check other references (e.g. Payroll runs, manual journals)
    unlinked_refs = list(GeneralLedgerEntry.objects.filter(
        sales_invoice__isnull=True,
        purchase_bill__isnull=True,
        credit_note__isnull=True,
        debit_note__isnull=True,
    ).exclude(reference='').values_list('reference', flat=True).distinct())

    for ref in unlinked_refs:
        entries = GeneralLedgerEntry.objects.filter(
            sales_invoice__isnull=True,
            purchase_bill__isnull=True,
            credit_note__isnull=True,
            debit_note__isnull=True,
            reference=ref
        )
        if entries.exists():
            balance_entry_group(entries, 'Voucher', ref, entries.first().created_by)

    # 3. Overall Tenant-Level Balance Check
    User = apps.get_model('users', 'User')
    for tenant in User.objects.all():
        tot_dr = GeneralLedgerEntry.objects.filter(created_by=tenant).aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = GeneralLedgerEntry.objects.filter(created_by=tenant).aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        global_diff = (tot_dr - tot_cr).quantize(Decimal('0.01'))

        if abs(global_diff) > Decimal('0.001'):
            round_off_acc, _ = Account.objects.get_or_create(
                code='4200',
                created_by=tenant,
                defaults={
                    'name': 'Rounding Off',
                    'account_type': 'revenue',
                    'description': 'Rounding Off Adjustment'
                }
            )
            from django.utils import timezone
            GeneralLedgerEntry.objects.create(
                date=timezone.now().date(),
                account=round_off_acc,
                debit=Decimal('0.00') if global_diff > 0 else abs(global_diff),
                credit=global_diff if global_diff > 0 else Decimal('0.00'),
                description="Global ledger round-off adjustment to balance GL",
                reference="ROUNDOFF-AUDIT-FIX",
                created_by=tenant
            )
            repaired_vouchers.append(f"Global Tenant {tenant.username} (GL Dr ₹{tot_dr} vs Cr ₹{tot_cr}, diff ₹{global_diff})")

    # 4. Write audit log entry
    if repaired_vouchers:
        log_msg = f"CA Audit Data Migration 0011: Fixed {len(repaired_vouchers)} unbalanced vouchers into 4200 Round Off: " + ", ".join(repaired_vouchers[:5])
        if AuditLog:
            try:
                first_entry = GeneralLedgerEntry.objects.first()
                if first_entry and first_entry.created_by:
                    AuditLog.objects.create(
                        tenant=first_entry.created_by,
                        user=first_entry.created_by,
                        user_email=getattr(first_entry.created_by, 'email', None),
                        action='UPDATE',
                        model_name='GeneralLedgerEntry',
                        object_id='MIGRATION_0011',
                        object_repr=log_msg[:255]
                    )
            except Exception:
                pass

        if ActionLog:
            try:
                first_entry = GeneralLedgerEntry.objects.first()
                if first_entry and first_entry.created_by:
                    ActionLog.objects.create(
                        user=first_entry.created_by,
                        action='UPDATE',
                        target_model='GeneralLedgerEntry',
                        target_id='MIGRATION_0011',
                        details={'message': log_msg, 'repaired': repaired_vouchers}
                    )
            except Exception:
                pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0032_salesorderitem_dispatched_quantity'),
        ('ledger', '0010_reclassify_gst_from_pf_esi'),
    ]

    operations = [
        migrations.RunPython(fix_unbalanced_entries, noop_reverse),
    ]

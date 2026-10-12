# Migration: Move PT Payable (2204 -> 2254) and Other Payroll Deductions (2205 -> 2255)
# CA AUDIT (Issue 11): PT Payable 2204 and Other Payroll Deductions 2205 sit in the 220x GST range.
# Move them into the 225x payroll range and update every reference, using an idempotent migration.

from django.db import migrations


def move_payroll_accounts(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')

    # Mapping from old code to new code and standard name
    code_mappings = {
        '2204': ('2254', 'Professional Tax Payable'),
        '2205': ('2255', 'Other Payroll Deductions Payable'),
    }

    for old_code, (new_code, standard_name) in code_mappings.items():
        # Find accounts using the old code
        old_accounts = Account.objects.filter(code=old_code)
        for old_acc in old_accounts:
            user = old_acc.created_by
            # Check if an account with new_code already exists for this tenant
            existing_target = Account.objects.filter(created_by=user, code=new_code).first()
            if existing_target:
                # Repoint all GL entries pointing to old_acc to existing_target
                GeneralLedgerEntry.objects.filter(account=old_acc).update(account=existing_target)
                # If old account has no entries left and is duplicate, delete it
                if not GeneralLedgerEntry.objects.filter(account=old_acc).exists():
                    old_acc.delete()
            else:
                # Update code and name to new 225x range
                old_acc.code = new_code
                if not old_acc.name or old_acc.name.strip().lower() in ['pt payable', 'professional tax']:
                    old_acc.name = standard_name
                old_acc.save(update_fields=['code', 'name'])

    # Also check accounts named PT Payable / Professional Tax or Other Payroll Deductions with non-225x code
    for acc in Account.objects.filter(name__icontains='Professional Tax'):
        if acc.code in ['2204', '2200']:
            user = acc.created_by
            existing_target = Account.objects.filter(created_by=user, code='2254').first()
            if existing_target and existing_target != acc:
                GeneralLedgerEntry.objects.filter(account=acc).update(account=existing_target)
                if not GeneralLedgerEntry.objects.filter(account=acc).exists():
                    acc.delete()
            else:
                acc.code = '2254'
                acc.save(update_fields=['code'])

    for acc in Account.objects.filter(name__icontains='Other Payroll Deductions'):
        if acc.code in ['2205', '2200']:
            user = acc.created_by
            existing_target = Account.objects.filter(created_by=user, code='2255').first()
            if existing_target and existing_target != acc:
                GeneralLedgerEntry.objects.filter(account=acc).update(account=existing_target)
                if not GeneralLedgerEntry.objects.filter(account=acc).exists():
                    acc.delete()
            else:
                acc.code = '2255'
                acc.save(update_fields=['code'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0008_generalledgerentry_payment_generalledgerentry_vendor_and_more'),
    ]

    operations = [
        migrations.RunPython(move_payroll_accounts, noop_reverse),
    ]

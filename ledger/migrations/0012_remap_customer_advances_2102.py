# Migration: Move Customer Advances (2101 -> 2102)
# Reclassify Customer Advances accounts from 2101 to standard 2102.
# Idempotent: running multiple times preserves references and makes no duplicate changes.

from django.db import migrations


def remap_customer_advances(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')

    # Accounts with code 2101 and name matching Customer Advances
    adv_accounts = Account.objects.filter(code='2101')
    for old_acc in adv_accounts:
        user = old_acc.created_by
        existing_target = Account.objects.filter(created_by=user, code='2102').first()
        if existing_target and existing_target != old_acc:
            # Repoint all GL entries pointing to old_acc to existing_target
            GeneralLedgerEntry.objects.filter(account=old_acc).update(account=existing_target)
            if not GeneralLedgerEntry.objects.filter(account=old_acc).exists():
                old_acc.delete()
        else:
            old_acc.code = '2102'
            if not old_acc.name or 'advance' in old_acc.name.lower():
                old_acc.name = 'Customer Advances'
            old_acc.save(update_fields=['code', 'name'])


def reverse_remap(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0011_fix_unbalanced_entries'),
    ]

    operations = [
        migrations.RunPython(remap_customer_advances, reverse_remap),
    ]

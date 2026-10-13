from django.db import migrations


def forwards(apps, schema_editor):
    # Import runtime repair (not historical models) so tax helpers stay current.
    # Guard: during partial migrate graphs, skip if DebitNote table is empty/unavailable.
    try:
        DebitNote = apps.get_model('billing', 'DebitNote')
        if not DebitNote.objects.exists():
            return
    except Exception:
        return
    from billing.debit_note_gst_repair import repair_debit_note_gst_reversals
    repair_debit_note_gst_reversals(stdout=None, dry_run=False)


def backwards(apps, schema_editor):
    # Irreversible data repair — no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0046_salesorder_customer_gstin_salesorderitem_hsn'),
        ('ledger', '0013_reconcile_sales_invoice_ledgers'),
        ('audit_log', '0003_add_download_action_choice'),
        ('users', '0020_user_default_preview_template_sections'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

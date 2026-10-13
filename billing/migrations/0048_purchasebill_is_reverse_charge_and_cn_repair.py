from django.db import migrations, models


def repair_legacy_credit_notes(apps, schema_editor):
    """Best-effort repair for CN-0002-style notes missing Output GST (idempotent)."""
    try:
        from billing.credit_note_gst_repair import repair_credit_note_gst
        repair_credit_note_gst(dry_run=False)
    except Exception:
        # Never block migrate on repair; can re-run via management path
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0047_repair_debit_note_gst_reversals'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasebill',
            name='is_reverse_charge',
            field=models.BooleanField(
                default=False,
                help_text='True when this purchase is under GST reverse charge (RCM).',
            ),
        ),
        migrations.RunPython(repair_legacy_credit_notes, migrations.RunPython.noop),
    ]

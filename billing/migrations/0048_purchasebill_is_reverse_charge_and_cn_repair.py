from django.db import migrations, models


def repair_legacy_credit_notes(apps, schema_editor):
    """Best-effort repair for CN-0002-style notes missing Output GST (idempotent).

    The repair helper uses the *live* models, which can have columns added by
    later migrations (e.g. CreditNote.gst_time_barred). On a fresh database
    those columns don't exist yet, so the query fails. On Postgres a failed
    query aborts the whole migration transaction, and swallowing the exception
    is not enough: migrate then dies in record_applied with
    "current transaction is aborted".

    So: skip when there are no credit notes (fresh DB / new tenant), and run
    the repair inside a savepoint so any failure is rolled back cleanly. On
    prod DBs this migration is already applied and does not re-run.
    """
    from django.db import transaction

    CreditNote = apps.get_model('billing', 'CreditNote')
    db_alias = schema_editor.connection.alias
    if not CreditNote.objects.using(db_alias).exists():
        return
    try:
        with transaction.atomic(using=db_alias):
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

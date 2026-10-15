from django.db import migrations, models


def remap_legacy_gst_rates(apps, schema_editor):
    """Map stored reference rows off removed 12%/28% slabs before choices tighten."""
    GSTRate = apps.get_model('references', 'GSTRate')
    GSTRate.objects.filter(rate=12).update(rate=18)
    GSTRate.objects.filter(rate=28).update(rate=40)


def noop_reverse(apps, schema_editor):
    # Irreversible content remap; choices restore is enough for schema rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('references', '0002_rename_references_gstrat_rate_idx_references__rate_5029ce_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(remap_legacy_gst_rates, noop_reverse),
        migrations.AlterField(
            model_name='gstrate',
            name='rate',
            field=models.IntegerField(
                choices=[(0, '0%'), (3, '3%'), (5, '5%'), (18, '18%'), (40, '40%')],
            ),
        ),
    ]

# UAT P1 #1: per-state PT with effective dates — Odisha versioned (per Rudra CA, to verify).
# Additive only: two nullable/defaulted columns, existing undated Odisha ₹0 row gets
# effective_from = 1 Apr 2026, legacy rows inserted. Nothing is deleted.

from django.db import migrations, models


def version_odisha(apps, schema_editor):
    from hr.pt_slab_data import ODISHA_NOTE_REPEAL, ODISHA_REPEAL_DATE, create_odisha_slabs
    Slab = apps.get_model('hr', 'ProfessionalTaxSlab')
    Slab.objects.filter(
        state_name__iexact='Odisha', pt_amount=0, effective_from__isnull=True, effective_to__isnull=True,
    ).update(effective_from=ODISHA_REPEAL_DATE, source_note=ODISHA_NOTE_REPEAL)
    if Slab.objects.exists():  # fresh DBs are seeded by seed_pt_slabs on startup
        create_odisha_slabs(Slab)


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0019_merge_hr_cloudinary_and_cash_salary'),
    ]

    operations = [
        migrations.AddField(
            model_name='professionaltaxslab',
            name='special_month',
            field=models.PositiveSmallIntegerField(blank=True, null=True, help_text='Month (1-12) in which february_amount applies; null = February. E.g. Odisha legacy ₹300 in March (last month of the FY).'),
        ),
        migrations.AddField(
            model_name='professionaltaxslab',
            name='source_note',
            field=models.CharField(blank=True, default='', max_length=500, help_text='Legal source / verification status of this slab.'),
        ),
        migrations.RunPython(version_odisha, migrations.RunPython.noop),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0014_professionaltaxslab_effective_dates'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hrmssettings',
            name='pf_apply_ceiling',
            field=models.BooleanField(
                default=True,
                help_text='When True, PF wage is capped at pf_wage_ceiling (statutory ₹15,000). Opt out per tenant to use full basic.',
            ),
        ),
        # Existing tenants: turn ceiling on unless they already opted into unrestricted PF
        migrations.RunSQL(
            sql="UPDATE hr_hrmssettings SET pf_apply_ceiling = TRUE WHERE pf_apply_ceiling = FALSE;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

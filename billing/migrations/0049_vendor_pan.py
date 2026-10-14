from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0048_purchasebill_is_reverse_charge_and_cn_repair'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='pan',
            field=models.CharField(
                blank=True,
                help_text='Vendor PAN (for TDS 194C 1%/2% and Sec 206AA). Auto-filled from GSTIN when present.',
                max_length=10,
                null=True,
            ),
        ),
    ]

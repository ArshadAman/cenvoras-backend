from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0021_salesinvoice_round_off'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesinvoice',
            name='po_number',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='salesinvoice',
            name='po_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salesinvoice',
            name='challan_number',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='salesinvoice',
            name='challan_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]

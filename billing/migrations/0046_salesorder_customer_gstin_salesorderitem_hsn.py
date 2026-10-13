from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0045_add_fields_to_sales_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='customer_gstin',
            field=models.CharField(blank=True, max_length=15, null=True),
        ),
        migrations.AddField(
            model_name='salesorderitem',
            name='hsn_sac_code',
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]

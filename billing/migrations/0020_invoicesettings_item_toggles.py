from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0019_recalculate_sales_invoice_totals'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoicesettings',
            name='require_item_batch',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_batch',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_description',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_discount',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_free_quantity',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_hsn',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_tax',
            field=models.BooleanField(default=True),
        ),
    ]

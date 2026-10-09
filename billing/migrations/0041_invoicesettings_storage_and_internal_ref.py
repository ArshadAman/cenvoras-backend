# Generated manually for Feature: Storage Condition and Internal Reference in InvoiceSettings

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0040_invoicesettings_show_item_manufacturer'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_storage_condition',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_internal_reference',
            field=models.BooleanField(default=False),
        ),
    ]

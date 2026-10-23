from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0052_document_number_unique_constraints'),
    ]

    operations = [
        migrations.AddField(
            model_name='transactionmeta',
            name='linked_challans',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Challan numbers and dates attached to this invoice, e.g. [{challan_number, challan_date}]',
            ),
        ),
    ]

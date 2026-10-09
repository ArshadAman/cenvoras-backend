from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0042_add_bank_account_id_to_documents'),
    ]

    operations = [
        migrations.AddField(
            model_name='partymeta',
            name='preview_templates',
            field=models.JSONField(blank=True, default=dict, help_text="Customer-specific preview template format per section, e.g. {'sales_invoice': 'classic', 'quotation': 'service'}"),
        ),
        migrations.AddField(
            model_name='transactionmeta',
            name='template_id',
            field=models.CharField(blank=True, help_text='ID of preview template/format selected for this invoice', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='template_id',
            field=models.CharField(blank=True, help_text='ID of preview template/format selected for this order', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='deliverychallan',
            name='template_id',
            field=models.CharField(blank=True, help_text='ID of preview template/format selected for this challan', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='quotation',
            name='template_id',
            field=models.CharField(blank=True, help_text='ID of preview template/format selected for this quotation', max_length=50, null=True),
        ),
    ]

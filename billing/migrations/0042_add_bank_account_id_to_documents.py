from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0041_invoicesettings_storage_and_internal_ref'),
    ]

    operations = [
        migrations.AddField(
            model_name='transactionmeta',
            name='bank_account_id',
            field=models.CharField(blank=True, help_text='ID of bank account selected for this invoice', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='bank_account_id',
            field=models.CharField(blank=True, help_text='ID of bank account selected for this order', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='deliverychallan',
            name='bank_account_id',
            field=models.CharField(blank=True, help_text='ID of bank account selected for this challan', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='quotation',
            name='bank_account_id',
            field=models.CharField(blank=True, help_text='ID of bank account selected for this quotation', max_length=50, null=True),
        ),
    ]

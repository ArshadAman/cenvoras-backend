from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0018_user_delivery_challan_prefix'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='delivery_challan_prefix',
            field=models.CharField(default='DC', help_text='Default delivery challan prefix for this user', max_length=20),
        ),
        migrations.AlterField(
            model_name='user',
            name='invoice_prefix',
            field=models.CharField(default='INV', help_text='Default invoice prefix for this user', max_length=20),
        ),
        migrations.AlterField(
            model_name='user',
            name='quotation_prefix',
            field=models.CharField(default='QT', help_text='Default quotation prefix for this user', max_length=20),
        ),
        migrations.AddField(
            model_name='user',
            name='bank_accounts',
            field=models.JSONField(blank=True, default=list, help_text='Up to 3 bank accounts for invoice billing'),
        ),
        migrations.AddField(
            model_name='user',
            name='default_bank_account_sections',
            field=models.JSONField(blank=True, default=dict, help_text='Default bank account ID per section'),
        ),
    ]

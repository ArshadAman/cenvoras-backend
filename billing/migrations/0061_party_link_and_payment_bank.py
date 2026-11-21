from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0060_item_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='linked_vendor',
            field=models.OneToOneField(
                blank=True,
                help_text='Same party when this customer is also a vendor.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_customer',
                to='billing.vendor',
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='bank_account_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Profile bank account id used for bank/UPI/cheque receipts.',
                max_length=50,
            ),
        ),
    ]

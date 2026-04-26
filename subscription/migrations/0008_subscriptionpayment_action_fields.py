from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0007_add_pending_plan_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscriptionpayment',
            name='action',
            field=models.CharField(choices=[('activate', 'Activate'), ('renew', 'Renew'), ('upgrade_now', 'Upgrade Now')], default='activate', max_length=20),
        ),
        migrations.AddField(
            model_name='subscriptionpayment',
            name='billing_details',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='subscriptionpayment',
            name='source_plan_code',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]

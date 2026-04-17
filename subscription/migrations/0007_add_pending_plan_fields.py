from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0006_update_plan_pricing_cashfree'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantsubscription',
            name='pending_plan',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pending_subscriptions', to='subscription.plan'),
        ),
        migrations.AddField(
            model_name='tenantsubscription',
            name='pending_plan_starts_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

from decimal import Decimal, ROUND_HALF_UP

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def discounted_cycle_price(monthly_price, months, discount):
    return money(Decimal(monthly_price) * Decimal(months) * (Decimal('1') - Decimal(discount)))


PLAN_PRICES = {
    'free': {
        'name': 'Starter',
        'monthly_price': Decimal('0.00'),
        'quarterly_price': Decimal('0.00'),
        'yearly_price': Decimal('0.00'),
        'original_monthly_price': Decimal('0.00'),
        'original_quarterly_price': Decimal('0.00'),
        'original_yearly_price': Decimal('0.00'),
    },
    'pro': {
        'name': 'Pro',
        'monthly_price': Decimal('1599.00'),
        'quarterly_price': discounted_cycle_price('1599.00', 3, '0.15'),
        'yearly_price': discounted_cycle_price('1599.00', 12, '0.30'),
        'original_monthly_price': Decimal('1899.00'),
        'original_quarterly_price': money(Decimal('1899.00') * Decimal('3')),
        'original_yearly_price': money(Decimal('1899.00') * Decimal('12')),
    },
    'business': {
        'name': 'Business',
        'monthly_price': Decimal('1999.00'),
        'quarterly_price': discounted_cycle_price('1999.00', 3, '0.15'),
        'yearly_price': discounted_cycle_price('1999.00', 12, '0.30'),
        'original_monthly_price': Decimal('2599.00'),
        'original_quarterly_price': money(Decimal('2599.00') * Decimal('3')),
        'original_yearly_price': money(Decimal('2599.00') * Decimal('12')),
    },
}


def configure_plan_prices(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')

    legacy_codes = {
        'free': 'starter',
        'pro': 'growth',
        'business': 'enterprise',
    }

    for code, values in PLAN_PRICES.items():
        plan = Plan.objects.filter(code=code).first()
        legacy_code = legacy_codes[code]
        if not plan:
            plan = Plan.objects.filter(code=legacy_code).first()
            if plan:
                plan.code = code

        if not plan:
            plan = Plan(code=code)

        for field, value in values.items():
            setattr(plan, field, value)

        plan.is_active = True
        plan.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('subscription', '0004_backfill_plan_limits_and_codes'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='original_monthly_price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='plan',
            name='original_quarterly_price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='plan',
            name='original_yearly_price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='plan',
            name='quarterly_price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.AddField(
            model_name='tenantsubscription',
            name='current_billing_cycle',
            field=models.CharField(choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')], default='monthly', max_length=20),
        ),
        migrations.AddField(
            model_name='tenantsubscription',
            name='pending_billing_cycle',
            field=models.CharField(blank=True, choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')], max_length=20, null=True),
        ),
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
        migrations.CreateModel(
            name='SubscriptionPaymentOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order_id', models.CharField(max_length=120, unique=True)),
                ('payment_session_id', models.CharField(blank=True, default='', max_length=255)),
                ('billing_cycle', models.CharField(choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')], default='monthly', max_length=20)),
                ('duration_days', models.PositiveIntegerField(default=30)),
                ('amount', models.DecimalField(decimal_places=2, default=0.0, max_digits=10)),
                ('status', models.CharField(choices=[('created', 'Created'), ('success', 'Success'), ('failed', 'Failed')], default='created', max_length=20)),
                ('failure_reason', models.TextField(blank=True, default='')),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('target_plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_orders', to='subscription.plan')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subscription_payment_orders', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(configure_plan_prices, noop_reverse),
    ]

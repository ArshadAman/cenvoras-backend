from decimal import Decimal, ROUND_HALF_UP
from django.db import migrations

def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def discounted_cycle_price(monthly_price, months, discount):
    return money(Decimal(monthly_price) * Decimal(months) * (Decimal('1') - Decimal(discount)))

def fix_plan_prices(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')
    
    # Prices from user request
    # Pro: 1599 monthly
    # Business: 1999 monthly
    
    plans_to_fix = {
        'pro': {
            'monthly_price': Decimal('1599.00'),
            'quarterly_price': discounted_cycle_price('1599.00', 3, '0.15'),
            'yearly_price': discounted_cycle_price('1599.00', 12, '0.30'),
            'original_monthly_price': Decimal('1599.00'),
        },
        'business': {
            'monthly_price': Decimal('1999.00'),
            'quarterly_price': discounted_cycle_price('1999.00', 3, '0.15'),
            'yearly_price': discounted_cycle_price('1999.00', 12, '0.30'),
            'original_monthly_price': Decimal('1999.00'),
        }
    }
    
    for code, values in plans_to_fix.items():
        plan = Plan.objects.filter(code=code).first()
        if plan:
            for field, value in values.items():
                setattr(plan, field, value)
            plan.save()

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('subscription', '0005_billing_cycles_payment_orders_and_prices'),
    ]
    operations = [
        migrations.RunPython(fix_plan_prices, noop),
    ]

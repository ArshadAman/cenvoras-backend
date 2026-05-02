from decimal import Decimal
from django.db import migrations

def forwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')
    
    # Pro Plan
    Plan.objects.filter(code='pro').update(
        monthly_price=Decimal('399.00'),
        description='Early Bird Plan: For growing shops that need more control'
    )
    
    # Business Plan
    Plan.objects.filter(code='business').update(
        monthly_price=Decimal('499.00'),
        description='Early Bird Plan: For larger teams and multi-location operations'
    )

def backwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')
    
    Plan.objects.filter(code='pro').update(
        monthly_price=Decimal('1599.00'),
        description='For growing shops that need more control'
    )
    Plan.objects.filter(code='business').update(
        monthly_price=Decimal('1999.00'),
        description='For larger teams and multi-location operations'
    )

class Migration(migrations.Migration):
    dependencies = [
        ('subscription', '0012_update_business_plan_limits'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

from django.db import migrations, models
from decimal import Decimal

def set_original_prices(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')
    Plan.objects.filter(code='pro').update(original_monthly_price=Decimal('1599.00'))
    Plan.objects.filter(code='business').update(original_monthly_price=Decimal('1999.00'))

class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0013_update_to_early_bird_pricing'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='original_monthly_price',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10),
        ),
        migrations.RunPython(set_original_prices),
    ]

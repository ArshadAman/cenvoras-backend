from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')

    Plan.objects.filter(code='pro').update(monthly_price=Decimal('1999.00'))
    Plan.objects.filter(code='business').update(monthly_price=Decimal('2599.00'))


def backwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')

    Plan.objects.filter(code='pro').update(monthly_price=Decimal('999.00'))
    Plan.objects.filter(code='business').update(monthly_price=Decimal('2499.00'))


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0005_subscriptionpayment'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0013_contract_fee_kyc_and_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='professionaltaxslab',
            name='effective_from',
            field=models.DateField(
                blank=True,
                help_text='First date this slab applies; null = always effective historically.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='professionaltaxslab',
            name='effective_to',
            field=models.DateField(
                blank=True,
                help_text='Last date this slab applies; null = currently open-ended.',
                null=True,
            ),
        ),
    ]

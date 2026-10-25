from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0053_transactionmeta_linked_challans'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasebill',
            name='place_of_supply',
            field=models.CharField(
                blank=True,
                help_text="Place of supply for the purchase (defaults to buyer's registered state).",
                max_length=50,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='supply_kind',
            field=models.CharField(
                blank=True,
                choices=[('goods', 'Goods'), ('services', 'Services')],
                default='',
                help_text='Drives GST on advances. Goods: no tax at receipt (Notfn 66/2017).',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='payment',
            name='adjusted_on',
            field=models.DateField(
                blank=True,
                help_text='Set when an advance is allocated to an invoice (GSTR-1 atadj).',
                null=True,
            ),
        ),
    ]

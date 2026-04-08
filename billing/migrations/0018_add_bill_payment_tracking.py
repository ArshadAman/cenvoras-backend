from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0017_salesinvoice_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchasebill',
            name='amount_paid',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='purchasebill',
            name='payment_status',
            field=models.CharField(choices=[('pending', 'Pending'), ('partial_paid', 'Partial Paid'), ('paid', 'Paid')], default='pending', max_length=20),
        ),
        migrations.AddField(
            model_name='salesinvoice',
            name='amount_paid',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='salesinvoice',
            name='payment_status',
            field=models.CharField(choices=[('pending', 'Pending'), ('partial_paid', 'Partial Paid'), ('paid', 'Paid')], default='pending', max_length=20),
        ),
    ]

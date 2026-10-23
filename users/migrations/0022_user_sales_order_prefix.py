from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0021_user_business_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='sales_order_prefix',
            field=models.CharField(
                default='SO',
                help_text='Default sales order prefix for this user',
                max_length=20,
            ),
        ),
    ]

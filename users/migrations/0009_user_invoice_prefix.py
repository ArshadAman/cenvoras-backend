from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_alter_user_phone'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='invoice_prefix',
            field=models.CharField(default='INV-', help_text='Default invoice prefix for this user', max_length=20),
        ),
    ]

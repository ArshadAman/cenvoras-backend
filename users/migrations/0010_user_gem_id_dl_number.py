from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_user_invoice_prefix'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='gem_id',
            field=models.CharField(blank=True, help_text='GEM ID (optional)', max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='dl_number',
            field=models.CharField(blank=True, help_text='DL number (optional)', max_length=50, null=True),
        ),
    ]
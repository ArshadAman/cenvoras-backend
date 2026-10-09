from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0019_user_bank_accounts_and_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='default_preview_template_sections',
            field=models.JSONField(blank=True, default=dict, help_text='Default preview template format per section'),
        ),
    ]

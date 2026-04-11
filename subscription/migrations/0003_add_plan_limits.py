from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0002_seed_default_plans_and_migrate_users'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='max_customers',
            field=models.IntegerField(default=-1, help_text='-1 for unlimited customers'),
        ),
        migrations.AddField(
            model_name='plan',
            name='max_team_members',
            field=models.IntegerField(default=0, help_text='Number of team members allowed'),
        ),
    ]

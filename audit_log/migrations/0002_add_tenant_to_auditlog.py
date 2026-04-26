from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('audit_log', '0001_initial'),
        ('users', '0009_user_invoice_prefix'),
    ]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='tenant',
            field=models.ForeignKey(
                blank=True, 
                help_text='The business/tenant account this log belongs to', 
                null=True, 
                on_delete=django.db.models.deletion.SET_NULL, 
                related_name='tenant_audit_logs', 
                to='users.user'
            ),
        ),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('billing', '0049_vendor_pan'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoicesettings',
            name='show_cenvora_footer',
            field=models.BooleanField(
                default=True,
                help_text="Show 'Made with Cenvora' footer on invoices. Default True for non-business plans.",
            ),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='allow_cenvora_footer_toggle',
            field=models.BooleanField(
                default=False,
                help_text='When True, tenant may turn show_cenvora_footer on/off. Only staff/superuser may set this.',
            ),
        ),
        migrations.CreateModel(
            name='TenantLearnedHsnGst',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hsn_code', models.CharField(db_index=True, max_length=20)),
                ('gst_rate', models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ('usage_count', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='learned_hsn_gst',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-usage_count', '-updated_at'],
                'unique_together': {('tenant', 'hsn_code')},
            },
        ),
        migrations.AddIndex(
            model_name='tenantlearnedhsngst',
            index=models.Index(fields=['tenant', 'hsn_code'], name='billing_ten_tenant__a1b2c3_idx'),
        ),
    ]

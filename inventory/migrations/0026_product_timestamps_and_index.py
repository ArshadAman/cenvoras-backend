from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0025_add_product_composite_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, blank=True, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['created_by', 'updated_at'], name='idx_prod_tenant_updated'),
        ),
    ]

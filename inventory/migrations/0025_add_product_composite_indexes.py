from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0024_product_internal_reference'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['created_by', 'is_active', 'name'], name='idx_prod_tenant_active_name'),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['created_by', 'item_code'], name='idx_prod_tenant_itemcode'),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['created_by', 'internal_reference'], name='idx_prod_tenant_intref'),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['created_by', 'manufacturer'], name='idx_prod_tenant_mfr'),
        ),
    ]

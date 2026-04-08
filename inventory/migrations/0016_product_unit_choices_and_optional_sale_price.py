from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0015_add_warranty_months_to_product'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='unit',
            field=models.CharField(
                choices=[
                    ('pcs', 'pcs'),
                    ('kg', 'kg'),
                    ('g', 'g'),
                    ('mg', 'mg'),
                    ('l', 'l'),
                    ('ml', 'ml'),
                    ('cm', 'cm'),
                    ('m', 'm'),
                    ('mm', 'mm'),
                    ('box', 'box'),
                    ('pack', 'pack'),
                    ('dozen', 'dozen'),
                    ('other', 'other'),
                ],
                default='pcs',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='product',
            name='sale_price',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True),
        ),
    ]

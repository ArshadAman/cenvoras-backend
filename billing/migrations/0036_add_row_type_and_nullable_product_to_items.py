from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0035_alter_deliverychallanitem_price_and_more'),
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesinvoiceitem',
            name='row_type',
            field=models.CharField(default='item', help_text="Type of row: 'item' or 'note'", max_length=20),
        ),
        migrations.AlterField(
            model_name='salesinvoiceitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='inventory.product'),
        ),
        migrations.AlterField(
            model_name='salesinvoiceitem',
            name='price',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='salesinvoiceitem',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name='deliverychallanitem',
            name='row_type',
            field=models.CharField(default='item', help_text="Type of row: 'item' or 'note'", max_length=20),
        ),
        migrations.AlterField(
            model_name='deliverychallanitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='inventory.product'),
        ),
        migrations.AddField(
            model_name='quotationitem',
            name='row_type',
            field=models.CharField(default='item', help_text="Type of row: 'item' or 'note'", max_length=20),
        ),
        migrations.AlterField(
            model_name='quotationitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='inventory.product'),
        ),
        migrations.AlterField(
            model_name='quotationitem',
            name='price',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='quotationitem',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='quotationitem',
            name='quantity',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='salesorderitem',
            name='row_type',
            field=models.CharField(default='item', help_text="Type of row: 'item' or 'note'", max_length=20),
        ),
        migrations.AlterField(
            model_name='salesorderitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='inventory.product'),
        ),
        migrations.AlterField(
            model_name='salesorderitem',
            name='price',
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AlterField(
            model_name='salesorderitem',
            name='amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name='salesorderitem',
            name='quantity',
            field=models.PositiveIntegerField(default=1),
        ),
    ]

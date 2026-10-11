from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0043_add_template_id_and_preview_templates'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='salesinvoiceitem',
            options={'ordering': ['created_at', 'id']},
        ),
        migrations.AddField(
            model_name='salesinvoiceitem',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, blank=True, null=True),
        ),
        migrations.AddField(
            model_name='invoicesettings',
            name='show_item_purchase_price',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='partymeta',
            name='shipping_addresses',
            field=models.JSONField(blank=True, default=list, help_text='List of saved shipping addresses for this customer'),
        ),
    ]

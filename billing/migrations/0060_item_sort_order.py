from collections import defaultdict

from django.db import migrations, models


def backfill_sort_order(apps, schema_editor):
    specs = [
        ('SalesInvoiceItem', 'sales_invoice_id', ('created_at', 'id')),
        ('SalesOrderItem', 'order_id', ('id',)),
        ('DeliveryChallanItem', 'challan_id', ('id',)),
        ('QuotationItem', 'quotation_id', ('id',)),
    ]
    for model_name, parent_field, order_fields in specs:
        Model = apps.get_model('billing', model_name)
        grouped = defaultdict(list)
        for item in Model.objects.all().order_by(*order_fields).iterator():
            grouped[getattr(item, parent_field)].append(item)
        to_update = []
        for items in grouped.values():
            for idx, item in enumerate(items):
                if item.sort_order != idx:
                    item.sort_order = idx
                    to_update.append(item)
        if to_update:
            Model.objects.bulk_update(to_update, ['sort_order'], batch_size=500)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0059_creditnote_entered_by_stock_restored'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesinvoiceitem',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='salesorderitem',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='deliverychallanitem',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='quotationitem',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name='salesinvoiceitem',
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.AlterModelOptions(
            name='salesorderitem',
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.AlterModelOptions(
            name='deliverychallanitem',
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.AlterModelOptions(
            name='quotationitem',
            options={'ordering': ['sort_order', 'id']},
        ),
        migrations.RunPython(backfill_sort_order, noop_reverse),
    ]

from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


DOCUMENT_UNIQUES = (
    ('PurchaseBill', 'bill_number', 'BILL'),
    ('PurchaseOrder', 'po_number', 'PO'),
    ('SalesOrder', 'order_number', 'SO'),
    ('DeliveryChallan', 'challan_number', 'DC'),
    ('Quotation', 'quotation_number', 'QT'),
)


def deduplicate_document_numbers(apps, schema_editor):
    """Rename extra copies so unique(created_by, number) can be applied."""
    for model_name, field_name, fallback_prefix in DOCUMENT_UNIQUES:
        Model = apps.get_model('billing', model_name)
        duplicates = (
            Model.objects.values('created_by', field_name)
            .annotate(cnt=Count('id'))
            .filter(cnt__gt=1)
        )
        for item in duplicates:
            number = item[field_name]
            if number is None:
                continue
            created_by_id = item['created_by']
            rows = list(
                Model.objects.filter(
                    created_by_id=created_by_id,
                    **{field_name: number},
                ).order_by('created_at', 'id')
            )
            for idx, row in enumerate(rows[1:], start=1):
                counter = idx
                base = number if str(number).strip() else fallback_prefix
                new_num = f"{base}-DUP{counter}"
                while Model.objects.filter(
                    created_by_id=created_by_id,
                    **{field_name: new_num},
                ).exists():
                    counter += 1
                    new_num = f"{base}-DUP{counter}"
                setattr(row, field_name, new_num)
                row.save(update_fields=[field_name])


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0051_rename_tenantlearnedhsngst_index'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(deduplicate_document_numbers, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='purchasebill',
            constraint=models.UniqueConstraint(
                condition=models.Q(('bill_number', ''), _negated=True),
                fields=('created_by', 'bill_number'),
                name='uniq_bill_number_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.UniqueConstraint(
                condition=models.Q(('po_number__isnull', False), models.Q(('po_number', ''), _negated=True)),
                fields=('created_by', 'po_number'),
                name='uniq_po_number_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='salesorder',
            constraint=models.UniqueConstraint(
                condition=models.Q(('order_number', ''), _negated=True),
                fields=('created_by', 'order_number'),
                name='uniq_order_number_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='deliverychallan',
            constraint=models.UniqueConstraint(
                condition=models.Q(('challan_number', ''), _negated=True),
                fields=('created_by', 'challan_number'),
                name='uniq_challan_number_per_tenant',
            ),
        ),
        migrations.AddConstraint(
            model_name='quotation',
            constraint=models.UniqueConstraint(
                condition=models.Q(('quotation_number', ''), _negated=True),
                fields=('created_by', 'quotation_number'),
                name='uniq_quotation_number_per_tenant',
            ),
        ),
    ]

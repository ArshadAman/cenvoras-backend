from django.db import migrations, models


def backfill_customer_address(apps, schema_editor):
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')

    for invoice in SalesInvoice.objects.select_related('customer').all():
        if invoice.customer_address:
            continue

        customer = getattr(invoice, 'customer', None)
        if customer and customer.address:
            invoice.customer_address = customer.address
            invoice.save(update_fields=['customer_address'])


def reverse_backfill_customer_address(apps, schema_editor):
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')
    SalesInvoice.objects.update(customer_address=None)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0022_salesinvoice_po_challan_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesinvoice',
            name='customer_address',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_customer_address, reverse_backfill_customer_address),
    ]

from decimal import Decimal

from django.db import migrations


def _line_amount(item):
    quantity = Decimal(str(item.quantity or 0))
    price = Decimal(str(item.price or 0))
    discount = Decimal(str(item.discount or 0))
    tax = Decimal(str(item.tax or 0))

    base = quantity * price
    discount_amount = (base * discount) / Decimal('100')
    taxable = base - discount_amount
    tax_amount = (taxable * tax) / Decimal('100')
    return (taxable + tax_amount).quantize(Decimal('0.01'))


def recalculate_invoice_totals(apps, schema_editor):
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')
    SalesInvoiceItem = apps.get_model('billing', 'SalesInvoiceItem')

    for invoice in SalesInvoice.objects.all().iterator():
        total = Decimal('0.00')

        for item in SalesInvoiceItem.objects.filter(sales_invoice=invoice).iterator():
            amount = _line_amount(item)
            if item.amount != amount:
                item.amount = amount
                item.save(update_fields=['amount'])
            total += amount

        if invoice.total_amount != total:
            invoice.total_amount = total

        if invoice.amount_paid > invoice.total_amount:
            invoice.amount_paid = invoice.total_amount

        if invoice.amount_paid <= 0:
            invoice.payment_status = 'pending'
        elif invoice.amount_paid < invoice.total_amount:
            invoice.payment_status = 'partial_paid'
        else:
            invoice.payment_status = 'paid'

        invoice.save(update_fields=['total_amount', 'amount_paid', 'payment_status'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0018_add_bill_payment_tracking'),
    ]

    operations = [
        migrations.RunPython(recalculate_invoice_totals, noop_reverse),
    ]

# Migration: Reconcile Sales Invoice Ledgers
# Rebuilds general ledger entries for any sales invoice where the ledger tax entries
# (CGST/SGST vs IGST) do not match the place of supply determination.
# Idempotent: running multiple times produces identical results.

from django.db import migrations


def reconcile_invoice_ledgers(apps, schema_editor):
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')
    from ledger.services import AccountingService
    from billing.gst_utils import get_seller_state_code, get_pos_state_code

    for invoice in SalesInvoice.objects.filter(status='final').select_related('customer'):
        tenant = getattr(invoice.created_by, 'active_tenant', invoice.created_by)
        seller_state_code = get_seller_state_code(tenant)
        pos = invoice.place_of_supply or (invoice.customer.state if invoice.customer else None)
        pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
        is_interstate = (seller_state_code != pos_code)

        gl_entries = GeneralLedgerEntry.objects.filter(sales_invoice_id=invoice.id).select_related('account')
        has_cgst_sgst = gl_entries.filter(account__code__in=['2201', '2202']).exists()
        has_igst = gl_entries.filter(account__code='2203').exists()

        mismatch = False
        if is_interstate and has_cgst_sgst:
            mismatch = True
        elif not is_interstate and has_igst:
            mismatch = True

        if mismatch:
            # Rebuild entries cleanly
            gl_entries.delete()
            from billing.models import SalesInvoice as ActualSalesInvoice
            actual_inv = ActualSalesInvoice.objects.get(pk=invoice.id)
            AccountingService.create_sales_invoice_entries(actual_inv)


def reverse_reconcile(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0012_remap_customer_advances_2102'),
    ]

    operations = [
        migrations.RunPython(reconcile_invoice_ledgers, reverse_reconcile),
    ]

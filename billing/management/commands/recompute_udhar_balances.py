from django.core.management.base import BaseCommand

from billing.balance_sync import recompute_customer_balance, recompute_invoice_amount_paid
from billing.models import Customer, SalesInvoice


class Command(BaseCommand):
    help = "Recompute invoice paid amounts and customer udhar balances from source records."

    def handle(self, *args, **options):
        invoice_ids = list(SalesInvoice.objects.values_list('id', flat=True))
        customer_ids = list(Customer.objects.values_list('id', flat=True))

        self.stdout.write(self.style.WARNING(f"Recomputing {len(invoice_ids)} invoices..."))
        for invoice_id in invoice_ids:
            recompute_invoice_amount_paid(invoice_id)

        self.stdout.write(self.style.WARNING(f"Recomputing {len(customer_ids)} customers..."))
        for customer_id in customer_ids:
            recompute_customer_balance(customer_id)

        self.stdout.write(self.style.SUCCESS("Udhar recomputation complete."))

from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Payment
from ledger.models import GeneralLedgerEntry
from ledger.services import AccountingService


class Command(BaseCommand):
    help = 'Rebuild payment ledger entries so invoice-less receipts post to Customer Advances instead of Accounts Receivable.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant-id',
            dest='tenant_id',
            required=True,
            help='Tenant/user ID to limit the rebuild to one tenant.',
        )

    def handle(self, *args, **options):
        tenant_id = options.get('tenant_id')

        payments = Payment.objects.filter(created_by_id=tenant_id).select_related('customer', 'invoice', 'created_by')
        if not payments.exists():
            self.stdout.write(self.style.WARNING('No payments found to rebuild.'))
            return

        with transaction.atomic():
            deleted_count, _ = GeneralLedgerEntry.objects.filter(
                created_by_id=tenant_id,
                reference__startswith='Payment Received',
            ).delete()

            recreated = 0
            for payment in payments:
                AccountingService.create_payment_received_entries(
                    customer=payment.customer,
                    amount=payment.amount,
                    description=payment.notes or f'Payment received - {payment.reference or ""}',
                    date=payment.date,
                    user=payment.created_by,
                    invoice=payment.invoice,
                    payment_id=payment.id,
                )
                recreated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Rebuilt payment ledger entries for {recreated} payments. Deleted {deleted_count} old payment ledger rows.'
        ))
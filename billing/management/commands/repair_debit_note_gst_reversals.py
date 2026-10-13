from django.core.management.base import BaseCommand

from billing.debit_note_gst_repair import repair_debit_note_gst_reversals


class Command(BaseCommand):
    help = (
        "Idempotently repair debit notes missing Input GST reversals "
        "(Dr AP gross, Cr Purchases taxable, Cr Input IGST/CGST/SGST)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report notes that would be repaired without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        self.stdout.write(self.style.NOTICE(
            "Repairing debit note GST reversals..." + (" (dry-run)" if dry_run else "")
        ))
        repaired, skipped = repair_debit_note_gst_reversals(stdout=self.stdout, dry_run=dry_run)
        self.stdout.write(self.style.SUCCESS(
            f"Done. repaired={repaired} skipped={skipped}"
        ))

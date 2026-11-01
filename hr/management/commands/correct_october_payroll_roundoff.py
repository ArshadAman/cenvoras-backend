"""
Management command to correct October 2026 payroll accrual rounding imbalance.

Inspects all GeneralLedgerEntry with reference="PAYROLL-ACCRUAL-2026-10",
computes the difference between credits and debits, and if an imbalance exists
(e.g. ₹0.09 due to net pay rounding up), posts a debit entry to Account 4200 (Rounding Off).
"""
import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from ledger.models import Account, AccountType, GeneralLedgerEntry


class Command(BaseCommand):
    help = 'Corrects October 2026 payroll accrual roundoff imbalance by posting a balancing entry to Account 4200.'

    def handle(self, *args, **options):
        ref = "PAYROLL-ACCRUAL-2026-10"
        entries = GeneralLedgerEntry.objects.filter(reference=ref)

        if not entries.exists():
            self.stdout.write(self.style.WARNING(f"No GeneralLedgerEntry records found with reference '{ref}'."))
            return

        # Group by tenant / created_by
        tenants = entries.values_list('created_by', flat=True).distinct()
        total_corrected = 0

        for tenant_id in tenants:
            tenant_entries = entries.filter(created_by_id=tenant_id)
            total_dr = sum((e.debit for e in tenant_entries), Decimal('0.00'))
            total_cr = sum((e.credit for e in tenant_entries), Decimal('0.00'))
            diff = (total_cr - total_dr).quantize(Decimal('0.01'))

            self.stdout.write(
                f"Tenant {tenant_id}: Total Dr = {total_dr}, Total Cr = {total_cr}, Diff (Cr - Dr) = {diff}"
            )

            if Decimal('0.00') < diff <= Decimal('1.00'):
                with transaction.atomic():
                    # Get or create Account 4200 ("Rounding Off")
                    first_entry = tenant_entries.first()
                    tenant = first_entry.created_by if first_entry else None

                    round_off_acc = Account.objects.filter(created_by=tenant, code='4200').first()
                    if not round_off_acc:
                        round_off_acc = Account.objects.filter(created_by=tenant, name__iexact='Rounding Off').first()
                    if not round_off_acc:
                        round_off_acc = Account.objects.create(
                            code='4200',
                            name='Rounding Off',
                            account_type=AccountType.REVENUE,
                            created_by=tenant,
                            is_active=True,
                            description='Rounding Off account',
                        )

                    entry = GeneralLedgerEntry.objects.create(
                        date=datetime.date(2026, 10, 31),
                        account=round_off_acc,
                        debit=diff,
                        credit=Decimal('0.00'),
                        description="Payroll rounding adjustment for October 2026",
                        reference=ref,
                        created_by=tenant,
                    )
                    total_corrected += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Successfully posted Dr ₹{diff} to Account 4200 ({round_off_acc.name}) "
                            f"dated 2026-10-31 for tenant {tenant_id} (Entry ID: {entry.id})."
                        )
                    )
            elif diff == Decimal('0.00'):
                self.stdout.write(
                    self.style.SUCCESS(f"Tenant {tenant_id}: Reference '{ref}' is already perfectly balanced.")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"Tenant {tenant_id}: Imbalance {diff} is outside expected range (0, 1.00]."
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"Finished October payroll roundoff check. Corrected {total_corrected} tenant(s).")
        )

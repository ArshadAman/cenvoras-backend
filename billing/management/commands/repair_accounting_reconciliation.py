from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from ledger.models import Account, GeneralLedgerEntry, AccountType
from ledger.services import AccountingService
from hr.services.hr_accounting_service import HRAccountingService
from billing.models import SalesInvoice, PurchaseBill, Payment
from billing.balance_sync import recompute_invoice_amount_paid, recompute_customer_balance

User = get_user_model()


class Command(BaseCommand):
    help = "Repairs account code collisions, re-links Sales Invoice Output GST entries, and ensures exact double-entry balancing for all invoices and bills."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting Accounting & GST Reconciliation Repair..."))

        users = User.objects.all()
        total_accounts_repaired = 0
        total_invoices_balanced = 0
        total_bills_balanced = 0
        total_gst_entries_relinked = 0

        hr_collision_map = {
            'salaries payable': '2250',
            'pf payable': '2251',
            'esi payable': '2252',
            'tds payable': '2253',
            'professional tax payable': '2254',
            'other payroll deductions payable': '2255',
        }

        for user in users:
            tenant = getattr(user, 'active_tenant', user)
            self.stdout.write(f"Processing tenant: {tenant.username} (ID: {tenant.id})")

            # 1. Fix HR account code collisions
            for acc in Account.objects.filter(created_by=tenant):
                clean_name = acc.name.strip().lower()
                if clean_name in hr_collision_map:
                    expected_code = hr_collision_map[clean_name]
                    if acc.code != expected_code:
                        self.stdout.write(f"  Fixing account code: {acc.name} ({acc.code} -> {expected_code})")
                        acc.code = expected_code
                        acc.save(update_fields=['code'])
                        total_accounts_repaired += 1

            # Ensure proper default accounts exist
            default_accounts = AccountingService.get_or_create_default_accounts(tenant)
            hr_accounts = HRAccountingService.get_or_create_hr_accounts(tenant)

            output_cgst = default_accounts.get('output_cgst')
            output_sgst = default_accounts.get('output_sgst')
            output_igst = default_accounts.get('output_igst')

            # 2. Re-link Sales Invoice entries that were erroneously posted to PF/ESI
            if output_cgst:
                updated = GeneralLedgerEntry.objects.filter(
                    created_by=tenant,
                    sales_invoice__isnull=False,
                    description__icontains='Output CGST'
                ).exclude(account=output_cgst).update(account=output_cgst)
                total_gst_entries_relinked += updated

            if output_sgst:
                updated = GeneralLedgerEntry.objects.filter(
                    created_by=tenant,
                    sales_invoice__isnull=False,
                    description__icontains='Output SGST'
                ).exclude(account=output_sgst).update(account=output_sgst)
                total_gst_entries_relinked += updated

            if output_igst:
                updated = GeneralLedgerEntry.objects.filter(
                    created_by=tenant,
                    sales_invoice__isnull=False,
                    description__icontains='Output IGST'
                ).exclude(account=output_igst).update(account=output_igst)
                total_gst_entries_relinked += updated

            # 3. Check and balance every finalized Sales Invoice
            invoices = SalesInvoice.objects.filter(created_by=tenant, status='final')
            for inv in invoices:
                entries = GeneralLedgerEntry.objects.filter(sales_invoice=inv, created_by=tenant)
                if not entries.exists():
                    continue

                tot_dr = sum((e.debit for e in entries), Decimal('0'))
                tot_cr = sum((e.credit for e in entries), Decimal('0'))
                diff = tot_dr - tot_cr

                if abs(diff) >= Decimal('0.01'):
                    self.stdout.write(f"  Balancing Sales Invoice {inv.invoice_number} (Dr: {tot_dr}, Cr: {tot_cr}, Diff: {diff})")
                    with transaction.atomic():
                        GeneralLedgerEntry.objects.filter(sales_invoice=inv).delete()
                        AccountingService.create_sales_invoice_entries(inv)
                    total_invoices_balanced += 1

            # 4. Check and balance every Purchase Bill
            bills = PurchaseBill.objects.filter(created_by=tenant)
            for bill in bills:
                entries = GeneralLedgerEntry.objects.filter(purchase_bill=bill, created_by=tenant)
                if not entries.exists():
                    continue

                tot_dr = sum((e.debit for e in entries), Decimal('0'))
                tot_cr = sum((e.credit for e in entries), Decimal('0'))
                diff = tot_dr - tot_cr

                if abs(diff) >= Decimal('0.01'):
                    self.stdout.write(f"  Balancing Purchase Bill {bill.bill_number} (Dr: {tot_dr}, Cr: {tot_cr}, Diff: {diff})")
                    with transaction.atomic():
                        GeneralLedgerEntry.objects.filter(purchase_bill=bill).delete()
                        AccountingService.create_purchase_bill_entries(bill)
                    total_bills_balanced += 1

        self.stdout.write(self.style.SUCCESS(
            f"Repair completed successfully!\n"
            f"- Accounts Repaired: {total_accounts_repaired}\n"
            f"- GST Entries Re-linked: {total_gst_entries_relinked}\n"
            f"- Invoices Balanced: {total_invoices_balanced}\n"
            f"- Bills Balanced: {total_bills_balanced}"
        ))

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from billing.models import Customer
from billing.services_customer_merge import find_duplicate_customers, merge_customers

User = get_user_model()


class Command(BaseCommand):
    help = "Find and merge duplicate customer records sharing the same GSTIN."

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            help='Filter by tenant username or email',
        )
        parser.add_argument(
            '--gstin',
            type=str,
            help='Filter by specific GSTIN',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report duplicates without performing any database modifications',
        )
        parser.add_argument(
            '--auto',
            action='store_true',
            help='Automatically merge all duplicates into the earliest created record',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        auto_merge = options.get('auto', False)
        target_tenant = options.get('tenant')
        target_gstin = options.get('gstin')

        tenants_qs = User.objects.filter(parent__isnull=True)
        if target_tenant:
            tenants_qs = tenants_qs.filter(email__iexact=target_tenant) | tenants_qs.filter(username__iexact=target_tenant)

        total_merged = 0

        for tenant in tenants_qs:
            clusters = find_duplicate_customers(tenant)
            if target_gstin:
                clusters = [c for c in clusters if c.get('key', '').upper() == target_gstin.strip().upper()]

            if not clusters:
                continue

            self.stdout.write(self.style.NOTICE(f"\n--- Tenant: {tenant} ({tenant.email or tenant.username}) ---"))
            for cluster in clusters:
                c_type = cluster['type']
                key = cluster['key']
                custs = cluster['customers']
                self.stdout.write(self.style.WARNING(f"\nDuplicate Cluster ({c_type.upper()}: {key}) - {len(custs)} records:"))
                for i, c in enumerate(custs):
                    self.stdout.write(f"  [{i+1}] ID: {c['id'][:8]}... | Name: {c['name']} | GSTIN: {c['gstin']} | Bal: {c['current_balance']} | Created: {c['created_at']}")

                if dry_run:
                    self.stdout.write(self.style.MIGRATE_HEADING("  [DRY-RUN] No changes made."))
                    continue

                if auto_merge:
                    primary_id = custs[0]['id']
                    for dup in custs[1:]:
                        dup_id = dup['id']
                        self.stdout.write(f"  -> Merging {dup_id[:8]} into primary {primary_id[:8]}...")
                        res = merge_customers(tenant, primary_id, dup_id)
                        self.stdout.write(self.style.SUCCESS(
                            f"     Done! New Balance: {res['new_balance']}. "
                            f"Reassigned: {res['reassigned_counts']}"
                        ))
                        total_merged += 1

        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDry run completed. Pass --auto without --dry-run to execute merges."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nMerge process completed. Total duplicate customer records merged: {total_merged}."))

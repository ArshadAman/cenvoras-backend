"""Backfill blank Employee.work_state from tenant/company state."""
from django.core.management.base import BaseCommand

from hr.models import Employee
from hr.services.work_state import resolve_work_state


class Command(BaseCommand):
    help = 'Set blank employee work_state from company/tenant state (for PT slabs).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        qs = Employee.objects.filter(work_state__in=['', None]).select_related('tenant')
        updated = 0
        for emp in qs.iterator():
            resolved = resolve_work_state(employee=emp, tenant=emp.tenant)
            if not resolved:
                continue
            updated += 1
            if not dry:
                emp.work_state = resolved
                emp.save(update_fields=['work_state'])
        self.stdout.write(self.style.SUCCESS(
            f"{'Would update' if dry else 'Updated'} {updated} employee(s)."
        ))

from django.core.management.base import BaseCommand
from audit_log.models import AuditLog
from django.db.models import F

class Command(BaseCommand):
    help = 'Populates the tenant field for existing audit logs based on the user'

    def handle(self, *args, **options):
        logs = AuditLog.objects.filter(tenant__isnull=True, user__isnull=False).select_related('user')
        count = 0
        self.stdout.write(f"Found {logs.count()} logs to migrate.")
        
        for log in logs:
            if log.user:
                log.tenant = log.user.active_tenant
                log.save(update_fields=['tenant'])
                count += 1
                if count % 100 == 0:
                    self.stdout.write(f"Migrated {count} logs...")
        
        self.stdout.write(self.style.SUCCESS(f"Successfully migrated {count} logs."))

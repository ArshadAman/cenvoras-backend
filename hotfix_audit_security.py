import os
import django
import sys

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from audit_log.models import AuditLog
from django.contrib.auth import get_user_model

User = get_user_model()

def hotfix_audit_logs():
    print("--- Audit Log Security Hotfix ---")
    
    # 1. Identify superuser IDs and common admin emails
    superusers = User.objects.filter(is_superuser=True)
    superuser_ids = list(superusers.values_list('id', flat=True))
    admin_emails = ['cenvoras@gmail.com']
    
    print(f"Found {len(superusers)} superusers.")
    
    # 2. Delete existing logs that shouldn't be visible to tenants
    # We delete them rather than just filtering to be safe against accidental exposure
    logs_to_delete = AuditLog.objects.filter(
        django.db.models.Q(user_id__in=superuser_ids) | 
        django.db.models.Q(user_email__in=admin_emails) |
        django.db.models.Q(user_email='system')
    )
    
    count = logs_to_delete.count()
    if count > 0:
        print(f"Deleting {count} sensitive audit logs...")
        logs_to_delete.delete()
        print("Done.")
    else:
        print("No sensitive logs found to delete.")

    print("--- Hotfix Complete ---")

if __name__ == "__main__":
    hotfix_audit_logs()

import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')

# Monkeypatch django-dbbackup to prevent it from appending .bin
# Cloudinary Raw Media Storage strictly rejects .bin files for security.
try:
    from dbbackup.db.postgresql import PgDumpConnector
    # Override the property/method that returns the extension
    original_init = PgDumpConnector.__init__
    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.extension = "psql" # Force .psql instead of letting it append .bin
    PgDumpConnector.__init__ = patched_init
except ImportError:
    pass

app = Celery('cenvoras')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.timezone = os.environ.get('CELERY_TIMEZONE', 'Asia/Kolkata')
app.conf.enable_utc = False

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

from celery.schedules import crontab

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

# Celery Beat Schedule for Automated Tasks
app.conf.beat_schedule = {
    'daily-database-backup': {
        'task': 'users.tasks.run_database_backup',
        # Run daily within 2AM-3AM IST window.
        'schedule': crontab(minute=int(os.environ.get('BACKUP_SCHEDULE_MINUTE', '15')), hour=2),
    },
    'subscription-expiry-notification-check': {
        'task': 'subscription.tasks.notify_subscription_expiry_windows',
        # Run every 15 minutes to catch 24h reminder and exact expiry windows reliably.
        'schedule': crontab(minute='*/15'),
    },
    'subscription-payment-pending-reconciliation': {
        'task': 'subscription.tasks.reconcile_pending_subscription_payments',
        # Run every 5 minutes to heal missed/delayed success webhooks.
        'schedule': crontab(minute='*/5'),
    },
}

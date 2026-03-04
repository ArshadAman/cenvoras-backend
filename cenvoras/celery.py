import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')

app = Celery('cenvoras')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

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
        # Run daily at midnight
        'schedule': crontab(minute=0, hour=0),
    },
}

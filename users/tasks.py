from celery import shared_task
from django.core.management import call_command
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@shared_task
def send_async_email(subject, message, recipient_list):
    """
    Asynchronously sends an email to prevent blocking the HTTP response.
    """
    try:
        from_email = settings.DEFAULT_FROM_EMAIL
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
        )
        logger.info(f"Successfully sent async email to {recipient_list}")
        return True
    except Exception as e:
        logger.error(f"Failed to send async email to {recipient_list}: {str(e)}")
        return False

@shared_task
def run_database_backup():
    """
    Automated job to backup the PostgreSQL database to Cloudinary storage.
    Includes `clean=True` to prune old backups based on settings.
    """
    logger.info("Starting automated database backup to Cloudinary...")
    try:
        call_command('dbbackup', clean=True)
        logger.info("Database backup completed successfully.")
        return True
    except Exception as e:
        logger.error(f"Database backup failed: {str(e)}")
        return False

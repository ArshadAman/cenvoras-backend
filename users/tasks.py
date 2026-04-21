import datetime
import gzip
import json
import logging
import os
import subprocess
import tempfile
import traceback

import requests as http_requests
from celery import shared_task
from cloudinary.api import delete_resources, resources
from cloudinary import config as cloudinary_config
from cloudinary.uploader import upload as cloudinary_upload
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from .models import ActionLog

logger = logging.getLogger(__name__)

CIRCUIT_KEY = "backup:circuit:open_until"
LAST_ALERT_KEY = "backup:circuit:last_alert_at"


def _log_action(user, action, details):
    try:
        ActionLog.objects.create(
            user=user,
            action=action,
            model_name="DatabaseBackup",
            object_id="global",
            details=details,
        )
    except Exception as exc:
        logger.error("Failed to persist backup ActionLog: %s", exc)


def _pg_dump_to_file(target_path):
    db = settings.DATABASES["default"]
    cmd = [
        "pg_dump",
        "--host", str(db.get("HOST") or "localhost"),
        "--port", str(db.get("PORT") or "5432"),
        "--username", str(db.get("USER")),
        "--format", "plain",
        "--no-owner",
        "--no-privileges",
        "--file", target_path,
        str(db.get("NAME")),
    ]
    env = os.environ.copy()
    env["PGPASSWORD"] = str(db.get("PASSWORD") or "")
    run = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if run.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed (code {run.returncode}): {run.stderr.strip() or run.stdout.strip()}"
        )
    return {
        "stdout": run.stdout[-2000:],
        "stderr": run.stderr[-2000:],
    }


def _gzip_file(source_path):
    gz_path = f"{source_path}.gz"
    with open(source_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
    return gz_path


def _validate_gzip_archive(gz_path):
    # Equivalent behavior to `gzip -t`: read through stream and verify CRC.
    with gzip.open(gz_path, "rb") as gz_file:
        while gz_file.read(1024 * 1024):
            pass


def _parse_cloudinary_created_at(value):
    if not value:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _resolve_backup_env():
    explicit = os.environ.get("BACKUP_ENV") or os.environ.get("APP_ENV")
    if explicit:
        return str(explicit).strip().lower().replace(" ", "-")
    # Fallback to debug-derived environment label.
    return "dev" if getattr(settings, "DEBUG", False) else "prod"


def _resolve_backup_version():
    explicit = os.environ.get("BACKUP_VERSION")
    if explicit:
        return str(explicit).strip()

    try:
        run = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode == 0 and run.stdout.strip():
            return f"git-{run.stdout.strip()}"
    except Exception:
        pass

    return "git-unknown"


def _upload_backup_to_cloudinary(file_path, backup_name):
    cloud_name = getattr(settings, "CLOUDINARY_STORAGE", {}).get("CLOUD_NAME", "")
    api_key = getattr(settings, "CLOUDINARY_STORAGE", {}).get("API_KEY", "")
    api_secret = getattr(settings, "CLOUDINARY_STORAGE", {}).get("API_SECRET", "")
    if not cloud_name or not api_key or not api_secret:
        raise RuntimeError(
            "Cloudinary backup is not configured. Missing CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET"
        )

    cloudinary_config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )

    folder = getattr(settings, "BACKUP_CLOUDINARY_FOLDER", "cenvoras/db_backups")
    backup_type = str(getattr(settings, "BACKUP_CLOUDINARY_TYPE", "private") or "private").strip().lower()
    result = cloudinary_upload(
        file_path,
        resource_type="raw",
        type=backup_type,
        folder=folder,
        public_id=backup_name,
        overwrite=False,
        invalidate=True,
        tags=["pgsql-backup", "daily"],
    )
    return {
        "public_id": result.get("public_id"),
        "version": result.get("version"),
        "secure_url": result.get("secure_url"),
        "bytes": result.get("bytes"),
    }


def _cleanup_old_cloudinary_backups(keep_count):
    folder = getattr(settings, "BACKUP_CLOUDINARY_FOLDER", "cenvoras/db_backups")
    backup_type = str(getattr(settings, "BACKUP_CLOUDINARY_TYPE", "private") or "private").strip().lower()
    result = resources(
        type=backup_type,
        resource_type="raw",
        prefix=f"{folder}/backup_",
        max_results=500,
    )
    backups = result.get("resources", [])
    backups_sorted = sorted(
        backups,
        key=lambda item: _parse_cloudinary_created_at(item.get("created_at")),
        reverse=True,
    )

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    age_threshold = now_utc - datetime.timedelta(days=7)

    # Delete if older than 7 days.
    old_by_age = [
        item for item in backups_sorted
        if _parse_cloudinary_created_at(item.get("created_at")) < age_threshold
    ]
    old_by_age_ids = {item.get("public_id") for item in old_by_age if item.get("public_id")}

    # Also enforce count cap: keep latest N backups.
    over_count = backups_sorted[keep_count:]
    over_count_ids = {item.get("public_id") for item in over_count if item.get("public_id")}

    ids_to_delete = sorted(old_by_age_ids.union(over_count_ids))
    deleted = []
    if ids_to_delete:
        delete_resources(ids_to_delete, resource_type="raw", type=backup_type, invalidate=True)
        deleted = ids_to_delete

    return {
        "found": len(backups_sorted),
        "kept": min(keep_count, len(backups_sorted)),
        "deleted_by_age_count": len(old_by_age_ids),
        "deleted_by_count_count": len(over_count_ids),
        "deleted_count": len(deleted),
        "deleted": deleted,
    }


def _send_ahasend_alert(subject, body):
    api_key = getattr(settings, "TRANSACTIONAL_EMAIL_API_KEY", "")
    base_url = (getattr(settings, "TRANSACTIONAL_EMAIL_API_URL", "") or "https://api.ahasend.com/v1").rstrip("/")
    sender_email = getattr(settings, "TRANSACTIONAL_EMAIL_SENDER_EMAIL", "noreply@cenvora.app")
    sender_name = getattr(settings, "TRANSACTIONAL_EMAIL_SENDER_NAME", "Cenvora")
    alert_to = getattr(settings, "BACKUP_ALERT_EMAIL", "cenvoras@gmail.com")
    send_endpoint = getattr(settings, "TRANSACTIONAL_EMAIL_SEND_ENDPOINT", "/email/send")

    if not api_key:
        raise RuntimeError("TRANSACTIONAL_EMAIL_API_KEY is missing")

    payload = {
        "from": {"email": sender_email, "name": sender_name},
        "recipients": [{"email": alert_to}],
        "content": {
            "subject": subject,
            "text_body": body,
            "html_body": body.replace("\n", "<br>"),
        },
    }
    response = http_requests.post(
        f"{base_url}{send_endpoint if str(send_endpoint).startswith('/') else '/' + str(send_endpoint)}",
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=int(getattr(settings, "TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS", 20)),
    )
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"AHASEND failed [{response.status_code}]: {response.text[:500]}")


def _open_circuit(now):
    open_seconds = int(getattr(settings, "BACKUP_CIRCUIT_OPEN_SECONDS", 21600))
    open_until = now + datetime.timedelta(seconds=open_seconds)
    cache.set(CIRCUIT_KEY, open_until.isoformat(), timeout=open_seconds)
    return open_until


@shared_task
def send_async_email(subject, message, recipient_list, force_cenvora_branding=False):
    """
    Asynchronously sends an email via AhaSend using Cenvora branding.
    """
    try:
        api_key = getattr(settings, 'TRANSACTIONAL_EMAIL_API_KEY', '')
        base_url = (getattr(settings, 'TRANSACTIONAL_EMAIL_API_URL', '') or 'https://api.ahasend.com/v1').rstrip('/')
        send_endpoint = getattr(settings, 'TRANSACTIONAL_EMAIL_SEND_ENDPOINT', '/email/send')
        timeout_seconds = int(getattr(settings, 'TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS', 20))

        from_email = getattr(settings, 'TRANSACTIONAL_EMAIL_SENDER_EMAIL', 'noreply@cenvora.app')
        default_from_name = getattr(settings, 'TRANSACTIONAL_EMAIL_SENDER_NAME', 'Cenvora')
        from_name = 'Cenvora' if force_cenvora_branding else default_from_name

        if not api_key:
            logger.error("Transactional email API key missing; unable to send async email")
            return False

        endpoint = send_endpoint if str(send_endpoint).startswith('/') else f"/{send_endpoint}"
        url = f"{base_url}{endpoint}"

        recipients = [{'email': email} for email in recipient_list if email]
        if not recipients:
            return False

        payload = {
            'from': {'email': from_email, 'name': from_name},
            'recipients': recipients,
            'content': {
                'subject': subject,
                'text_body': message,
                'html_body': str(message).replace('\n', '<br>'),
            },
        }

        response = http_requests.post(
            url,
            headers={
                'X-Api-Key': api_key,
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=timeout_seconds,
        )

        if response.status_code not in (200, 201, 202):
            logger.error("AhaSend rejected async email: status=%s body=%s", response.status_code, response.text[:500])
            return False

        logger.info("Successfully sent async email to %s", recipient_list)
        return True
    except Exception as e:
        logger.error("Failed to send async email to %s: %s", recipient_list, str(e))
        return False


@shared_task
def run_database_backup(triggered_by_user_id=None, manual=False):
    """
    Create PostgreSQL backup and upload to Cloudinary using timestamped filename.
    Flow: pg_dump -> gzip -> validate -> upload -> keep last 7 backups.
    Retries 3 times, then opens circuit and alerts on failure.
    """
    now = timezone.localtime()
    user = None
    if triggered_by_user_id:
        try:
            user = get_user_model().objects.filter(id=triggered_by_user_id).first()
        except Exception:
            user = None

    open_until_raw = cache.get(CIRCUIT_KEY)
    if open_until_raw:
        try:
            open_until = datetime.datetime.fromisoformat(open_until_raw)
            if timezone.is_naive(open_until):
                open_until = timezone.make_aware(open_until, timezone.get_current_timezone())
        except Exception:
            open_until = None

        if open_until and now < open_until:
            msg = f"Backup skipped because circuit is open until {open_until.isoformat()}"
            logger.error(msg)
            _log_action(user, "BACKUP_SKIPPED_CIRCUIT_OPEN", {"open_until": open_until.isoformat(), "manual": manual})

            last_alert = cache.get(LAST_ALERT_KEY)
            if not last_alert:
                subject = "[Cenvoras] Backup Skipped: Circuit Open"
                body = f"{msg}\n\nTime: {now.isoformat()}"
                try:
                    _send_ahasend_alert(subject, body)
                except Exception as alert_exc:
                    logger.error("Failed to send circuit-open alert email: %s", alert_exc)
                cache.set(LAST_ALERT_KEY, now.isoformat(), timeout=3600)
            return False

    max_attempts = int(getattr(settings, "BACKUP_MAX_ATTEMPTS", 3))
    keep_count = int(getattr(settings, "DBBACKUP_CLEANUP_KEEP", 7))
    failures = []

    for attempt in range(1, max_attempts + 1):
        temp_sql_path = None
        temp_gz_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
                temp_sql_path = tmp.name

            now_local = timezone.localtime()
            date_part = now_local.strftime("%Y-%m-%d")
            time_part = now_local.strftime("%H-%M-%S")
            env_part = _resolve_backup_env()
            version_part = _resolve_backup_version()
            backup_name = f"backup_{env_part}_{date_part}_{time_part}_{version_part}.sql.gz"

            dump_output = _pg_dump_to_file(temp_sql_path)
            temp_gz_path = _gzip_file(temp_sql_path)
            _validate_gzip_archive(temp_gz_path)

            cloudinary_result = _upload_backup_to_cloudinary(temp_gz_path, backup_name)
            cleanup_result = _cleanup_old_cloudinary_backups(keep_count)

            details = {
                "attempt": attempt,
                "manual": manual,
                "backup_name": backup_name,
                "dump": dump_output,
                "cloudinary": cloudinary_result,
                "cleanup": cleanup_result,
                "completed_at": timezone.localtime().isoformat(),
            }
            logger.info("Database backup successful: %s", json.dumps(details, default=str))
            _log_action(user, "BACKUP_SUCCESS", details)
            cache.delete(CIRCUIT_KEY)
            cache.delete(LAST_ALERT_KEY)
            return True
        except Exception as exc:
            failure = {
                "attempt": attempt,
                "error": str(exc),
                "traceback": traceback.format_exc()[-4000:],
                "timestamp": timezone.localtime().isoformat(),
            }
            failures.append(failure)
            logger.error("Backup attempt %s/%s failed: %s", attempt, max_attempts, exc)
        finally:
            if temp_sql_path and os.path.exists(temp_sql_path):
                try:
                    os.remove(temp_sql_path)
                except OSError:
                    pass
            if temp_gz_path and os.path.exists(temp_gz_path):
                try:
                    os.remove(temp_gz_path)
                except OSError:
                    pass

    open_until = _open_circuit(timezone.localtime())
    fail_details = {
        "manual": manual,
        "open_until": open_until.isoformat(),
        "attempts": failures,
    }
    _log_action(user, "BACKUP_FAILED", fail_details)

    subject = "[Cenvoras] Database Backup Failed After 3 Attempts"
    body = (
        "Automated PostgreSQL backup to Cloudinary failed.\n\n"
        f"Time: {timezone.localtime().isoformat()}\n"
        f"Circuit Open Until: {open_until.isoformat()}\n\n"
        f"Failure Details:\n{json.dumps(fail_details, indent=2, default=str)}"
    )
    try:
        _send_ahasend_alert(subject, body)
    except Exception as alert_exc:
        logger.error("Failed to send backup failure alert email: %s", alert_exc)

    return False

import datetime

from cloudinary import config as cloudinary_config
from cloudinary.api import resources
from django.conf import settings
from django.core.management.base import BaseCommand

from users.tasks import run_database_backup


class Command(BaseCommand):
    help = "Verify timestamped Cloudinary backups and optionally execute a backup run immediately."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-backup",
            action="store_true",
            help="Run backup task immediately before verification",
        )

    def handle(self, *args, **options):
        if options["run_backup"]:
            self.stdout.write(self.style.WARNING("Running backup now..."))
            ok = run_database_backup(manual=True)
            if ok:
                self.stdout.write(self.style.SUCCESS("Backup run completed successfully."))
            else:
                self.stdout.write(self.style.ERROR("Backup run failed. Check logs and alerts."))

        cloud_name = getattr(settings, "CLOUDINARY_STORAGE", {}).get("CLOUD_NAME", "")
        api_key = getattr(settings, "CLOUDINARY_STORAGE", {}).get("API_KEY", "")
        api_secret = getattr(settings, "CLOUDINARY_STORAGE", {}).get("API_SECRET", "")
        if not cloud_name or not api_key or not api_secret:
            self.stdout.write(
                self.style.ERROR(
                    "Cloudinary is not configured. Missing CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET."
                )
            )
            return

        cloudinary_config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )

        folder = getattr(settings, "BACKUP_CLOUDINARY_FOLDER", "cenvoras/db_backups")
        backup_type = str(getattr(settings, "BACKUP_CLOUDINARY_TYPE", "private") or "private").strip().lower()
        keep_count = int(getattr(settings, "DBBACKUP_CLEANUP_KEEP", 7))
        result = resources(type=backup_type, resource_type="raw", prefix=f"{folder}/backup_", max_results=500)
        resources_list = result.get("resources", [])

        backups = sorted(
            resources_list,
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )

        self.stdout.write("\nLatest Cloudinary Backups:")
        if not backups:
            self.stdout.write(self.style.WARNING("- No backups found."))
        else:
            for item in backups[:keep_count]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"- {item.get('public_id')} | bytes={item.get('bytes')} | created_at={item.get('created_at')}"
                    )
                )

        excess = max(0, len(backups) - keep_count)
        if excess > 0:
            self.stdout.write(self.style.WARNING(f"\nBackups above retention limit: {excess}"))
        else:
            self.stdout.write(self.style.SUCCESS("\nRetention looks healthy (<= keep limit)."))

        self.stdout.write(
            f"\nVerification timestamp: {datetime.datetime.now().isoformat()}"
        )

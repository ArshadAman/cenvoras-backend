from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ActionLog, User
from .tasks import run_database_backup


@admin.action(description="Trigger PostgreSQL backup to Cloudinary")
def trigger_database_backup(modeladmin, request, queryset):
	if not request.user.is_superuser:
		messages.error(request, "Only super admins can trigger backups.")
		return

	run_database_backup.delay(triggered_by_user_id=str(request.user.id), manual=True)
	messages.success(
		request,
		"Backup task queued. Check Celery logs and Action Logs for status.",
	)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
	actions = [trigger_database_backup]


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
	list_display = ("timestamp", "user", "action", "model_name", "object_id")
	list_filter = ("action", "model_name", "timestamp")
	search_fields = ("model_name", "object_id", "action")

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


@admin.action(description="Grant VIP (lifetime free) access")
def grant_vip_access(modeladmin, request, queryset):
	updated = queryset.update(is_lifetime_free=True)
	messages.success(request, f"Granted VIP access to {updated} user(s).")


@admin.action(description="Revoke VIP (lifetime free) access")
def revoke_vip_access(modeladmin, request, queryset):
	updated = queryset.update(is_lifetime_free=False)
	messages.success(request, f"Revoked VIP access for {updated} user(s).")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
	actions = [trigger_database_backup, grant_vip_access, revoke_vip_access]
	list_display = (
		"username",
		"email",
		"business_name",
		"is_lifetime_free",
		"subscription_status",
		"is_staff",
		"is_active",
	)
	list_filter = (
		"is_lifetime_free",
		"subscription_status",
		"is_staff",
		"is_active",
	)
	search_fields = ("username", "email", "business_name", "phone")

	fieldsets = DjangoUserAdmin.fieldsets + (
		(
			"Subscription & Access",
			{
				"fields": (
					"is_lifetime_free",
					"subscription_status",
					"trial_ends_at",
					"subscription_tier",
				),
			},
		),
		(
			"Business Profile",
			{
				"fields": (
					"business_name",
					"phone",
					"gstin",
					"business_address",
					"state",
					"invoice_prefix",
				),
			},
		),
		(
			"Team & Permissions",
			{
				"fields": (
					"role",
					"parent",
					"permissions",
					"profile_completed",
					"last_login_at",
				),
			},
		),
	)


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
	list_display = ("timestamp", "user", "action", "model_name", "object_id")
	list_filter = ("action", "model_name", "timestamp")
	search_fields = ("model_name", "object_id", "action")

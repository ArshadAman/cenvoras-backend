from django.contrib import admin
from .models import Feature, Plan, TenantSubscription, SubscriptionPayment

@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'monthly_price', 'max_managers', 'is_active')
    filter_horizontal = ('features',)

@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'tenant',
        'plan',
        'status',
        'current_period_end',
        'cancel_at_period_end',
        'pending_plan',
        'pending_plan_starts_at',
    )
    list_filter = ('status', 'plan', 'cancel_at_period_end')
    search_fields = ('tenant__email', 'tenant__business_name')
    actions = ['clear_scheduled_plan_changes']

    @admin.action(description='Clear scheduled plan changes (pending plan and cancel-at-period-end)')
    def clear_scheduled_plan_changes(self, request, queryset):
        updated = queryset.update(
            pending_plan=None,
            pending_plan_starts_at=None,
            cancel_at_period_end=False,
        )
        self.message_user(request, f'Cleared scheduled plan state for {updated} subscription(s).')


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'plan', 'amount', 'currency', 'status', 'order_id', 'created_at')
    list_filter = ('status', 'plan', 'provider')
    search_fields = ('tenant__email', 'tenant__business_name', 'order_id', 'cf_order_id', 'cf_payment_id')

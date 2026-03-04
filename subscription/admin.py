from django.contrib import admin
from .models import Feature, Plan, TenantSubscription

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
    list_display = ('tenant', 'plan', 'status', 'current_period_end')
    list_filter = ('status', 'plan')
    search_fields = ('tenant__email', 'tenant__business_name')

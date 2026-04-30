from django.contrib import admin
from .models import HSNCode, GSTRate


@admin.register(HSNCode)
class HSNCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'description', 'category', 'created_at')
    list_filter = ('category', 'created_at')
    search_fields = ('code', 'description', 'category')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    fieldsets = (
        ('Basic Information', {
            'fields': ('code', 'description', 'category', 'slug')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description', 'long_description')
        }),
        ('Metadata', {
            'fields': ('external_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(GSTRate)
class GSTRateAdmin(admin.ModelAdmin):
    list_display = ('rate', 'category', 'hsn_codes', 'effective_from')
    list_filter = ('rate', 'effective_from', 'created_at')
    search_fields = ('category', 'hsn_codes', 'notes')
    readonly_fields = ('slug', 'created_at', 'updated_at')
    fieldsets = (
        ('Rate Information', {
            'fields': ('category', 'rate', 'hsn_codes', 'effective_from')
        }),
        ('Details', {
            'fields': ('notes', 'long_description')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description')
        }),
        ('Metadata', {
            'fields': ('slug', 'external_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

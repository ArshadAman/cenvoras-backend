from django.contrib import admin
from .models import Customer, PurchaseBill, SalesInvoice, SalesInvoiceItem, PurchaseBillItem, PurchaseOrder, PurchaseOrderItem
from .models_sidecar import InvoiceSettings, TenantLearnedHsnGst

admin.site.register(Customer)
admin.site.register(PurchaseBill)
admin.site.register(SalesInvoice)
admin.site.register(SalesInvoiceItem)
admin.site.register(PurchaseBillItem)
admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderItem)


@admin.register(InvoiceSettings)
class InvoiceSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'template_name',
        'show_cenvora_footer',
        'allow_cenvora_footer_toggle',
    )
    list_filter = ('show_cenvora_footer', 'allow_cenvora_footer_toggle')
    search_fields = ('user__username', 'user__business_name', 'user__email')
    readonly_fields = ()
    fieldsets = (
        (None, {
            'fields': ('user', 'template_name', 'print_offset_x', 'print_offset_y'),
        }),
        ('Cenvora footer', {
            'fields': ('show_cenvora_footer', 'allow_cenvora_footer_toggle'),
            'description': 'Staff only: grant allow_cenvora_footer_toggle so the tenant can flip the footer in app settings.',
        }),
        ('Copy', {
            'fields': ('header_text', 'footer_text', 'terms_conditions'),
        }),
        ('Item columns', {
            'fields': (
                'show_item_description',
                'show_item_hsn',
                'show_item_batch',
                'require_item_batch',
                'show_item_free_quantity',
                'show_item_discount',
                'show_item_tax',
                'show_item_manufacturer',
                'show_item_storage_condition',
                'show_item_internal_reference',
                'show_item_purchase_price',
            ),
        }),
    )


@admin.register(TenantLearnedHsnGst)
class TenantLearnedHsnGstAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'hsn_code', 'gst_rate', 'usage_count', 'updated_at')
    list_filter = ('gst_rate',)
    search_fields = ('hsn_code', 'tenant__username', 'tenant__business_name')

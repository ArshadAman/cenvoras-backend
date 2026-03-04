from django.urls import path
from .views import (
    PublicProductListView, PublicOrderCreateView,
    SendInvoiceNotificationView, SendCustomEmailView, SendPaymentRemindersView,
    NotificationLogListView,
    NotificationTemplateListView,
    BarcodeLookupView,
    DataExportView, DataImportView,
    ApiKeyListCreateView, ApiKeyDeleteView,
)

urlpatterns = [
    # Public API (API Key Auth)
    path('products/', PublicProductListView.as_view(), name='public-products'),
    path('orders/', PublicOrderCreateView.as_view(), name='public-orders'),
    
    # Notifications
    path('notifications/send/', SendInvoiceNotificationView.as_view(), name='send-notification'),
    path('notifications/send-email/', SendCustomEmailView.as_view(), name='send-custom-email'),
    path('notifications/send-reminders/', SendPaymentRemindersView.as_view(), name='send-payment-reminders'),
    path('notifications/logs/', NotificationLogListView.as_view(), name='notification-logs'),
    path('notifications/templates/', NotificationTemplateListView.as_view(), name='notification-templates'),
    
    # Barcode
    path('barcode/<str:barcode>/', BarcodeLookupView.as_view(), name='barcode-lookup'),
    
    # Backup & Restore
    path('backup/export/', DataExportView.as_view(), name='data-export'),
    path('backup/import/', DataImportView.as_view(), name='data-import'),
    
    # API Keys
    path('api-keys/', ApiKeyListCreateView.as_view(), name='api-key-list'),
    path('api-keys/<uuid:pk>/', ApiKeyDeleteView.as_view(), name='api-key-delete'),
]

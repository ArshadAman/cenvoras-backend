from django.urls import path
from .views import (
    purchase_bill_detail, purchase_bill_update_delete,
    sales_invoice_detail, sales_invoice_list_create, sales_invoice_update_delete, purchase_bill_list_create
)
from . import customer_views
from . import payment_views
from . import report_views
from . import views_sidecar

urlpatterns = [
    path('purchase-bills/', purchase_bill_list_create, name='purchase_bill_list_create'),
    path('purchase-bills/<uuid:pk>/', purchase_bill_detail, name='purchase_bill_detail'),
    path('purchase-bills/<uuid:pk>/edit/', purchase_bill_update_delete, name='purchase_bill_update_delete'),
    path('sales-invoices/', sales_invoice_list_create, name='sales_invoice_list_create'),
    path('sales-invoices/<uuid:pk>/', sales_invoice_detail, name='sales_invoice_detail'),
    path('sales-invoices/<uuid:pk>/edit/', sales_invoice_update_delete, name='sales_invoice_update_delete'),
    path('customers/', customer_views.customer_list_create, name='customer_list_create'),
    path('customers/<uuid:pk>/', customer_views.customer_detail, name='customer_detail'),
    path('customers/<uuid:pk>/edit/', customer_views.customer_update_delete, name='customer_update_delete'),
    
    
    # Payments
    path('payments/', payment_views.payment_list_create, name='payment_list_create'),
    path('payments/<uuid:pk>/', payment_views.payment_detail, name='payment_detail'),
    
    # Reports
    path('reports/overdue-bills/', report_views.overdue_bills_report, name='overdue_bills_report'),

    # Sidecar Features (Sales Orders, Challans, Settings)
    path('sales-orders/', views_sidecar.sales_order_list_create, name='sales_order_list_create'),
    path('sales-orders/<uuid:pk>/', views_sidecar.sales_order_detail, name='sales_order_detail'),
    path('sales-orders/<uuid:pk>/convert_to_invoice/', views_sidecar.convert_order_to_invoice, name='convert_order_to_invoice'),
    
    path('delivery-challans/', views_sidecar.delivery_challan_list_create, name='delivery_challan_list_create'),
    path('delivery-challans/<uuid:pk>/', views_sidecar.delivery_challan_detail, name='delivery_challan_detail'),
    
    path('invoice-settings/', views_sidecar.invoice_settings_view, name='invoice_settings_view'),
]
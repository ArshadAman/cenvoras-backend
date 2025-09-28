from django.urls import path
from .views import (
    purchase_bill_detail, purchase_bill_update_delete,
    sales_invoice_detail, sales_invoice_list_create, sales_invoice_update_delete, purchase_bill_list_create
)
from . import customer_views

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
]
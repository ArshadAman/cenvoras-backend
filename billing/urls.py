from django.urls import path
from .views import (
    PurchaseBillListCreateView, purchase_bill_detail, purchase_bill_update_delete,
    SalesInvoiceListCreateView, sales_invoice_detail, sales_invoice_update_delete
)

urlpatterns = [
    path('purchase-bills/', PurchaseBillListCreateView.as_view(), name='purchase_bill_list_create'),
    path('purchase-bills/<uuid:pk>/', purchase_bill_detail, name='purchase_bill_detail'),
    path('sales-invoices/', SalesInvoiceListCreateView.as_view(), name='sales_invoice_list_create'),
    path('sales-invoices/<uuid:pk>/', sales_invoice_detail, name='sales_invoice_detail'),
    path('purchase-bills/<uuid:pk>/edit/', purchase_bill_update_delete, name='purchase_bill_update_delete'),
    path('sales-invoices/<uuid:pk>/edit/', sales_invoice_update_delete, name='sales_invoice_update_delete'),
]
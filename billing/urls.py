from django.urls import path
from .views import (
    purchase_bill_detail, purchase_bill_update_delete,
    sales_invoice_detail, sales_invoice_list_create, sales_invoice_update_delete, purchase_bill_list_create,
    vendor_products, get_next_invoice_number, sales_summary_analytics, upload_sales_invoices_csv,
    export_sales_invoices_csv
)
from . import customer_views
from . import vendor_views
from . import payment_views
from . import report_views
from . import views_sidecar
from . import gst_views
from . import returns_views
from . import views

urlpatterns = [
    path('purchase-bills/', purchase_bill_list_create, name='purchase_bill_list_create'),
    path('purchase-bills/<uuid:pk>/', purchase_bill_detail, name='purchase_bill_detail'),
    path('purchase-bills/<uuid:pk>/edit/', purchase_bill_update_delete, name='purchase_bill_update_delete'),
    path('vendor-products/', vendor_products, name='vendor_products'),
    path('sales-invoices/', sales_invoice_list_create, name='sales_invoice_list_create'),
    path('sales-invoices/next-number/', get_next_invoice_number, name='get_next_invoice_number'),
    path('sales-invoices/analytics/', sales_summary_analytics, name='sales_summary_analytics'),
    path('sales-invoices/export-csv/', export_sales_invoices_csv, name='export_sales_invoices_csv'),
    path('sales-invoices/<uuid:pk>/', sales_invoice_detail, name='sales_invoice_detail'),
    path('sales-invoices/<uuid:pk>/edit/', sales_invoice_update_delete, name='sales_invoice_update_delete'),
    path('upload-sales-invoices-csv/', upload_sales_invoices_csv, name='upload_sales_invoices_csv'),
    path('customers/', customer_views.customer_list_create, name='customer_list_create'),
    path('customers/<uuid:pk>/', customer_views.customer_detail, name='customer_detail'),
    path('customers/<uuid:pk>/edit/', customer_views.customer_update_delete, name='customer_update_delete'),
    
    path('vendors/', vendor_views.vendor_list_create, name='vendor_list_create'),
    path('vendors/<uuid:pk>/', vendor_views.vendor_detail, name='vendor_detail'),
    path('vendors/<uuid:pk>/edit/', vendor_views.vendor_update_delete, name='vendor_update_delete'),
    
    # Payments
    path('payments/', payment_views.payment_list_create, name='payment_list_create'),
    path('payments/<uuid:pk>/', payment_views.payment_detail, name='payment_detail'),
    
    # Reports
    path('reports/overdue-bills/', report_views.overdue_bills_report, name='overdue_bills_report'),
    path('reports/item-pl/', report_views.item_wise_pl_report, name='item_wise_pl_report'),

    # Sidecar Features (Sales Orders, Challans, Settings)
    path('sales-orders/', views_sidecar.sales_order_list_create, name='sales_order_list_create'),
    path('sales-orders/<uuid:pk>/', views_sidecar.sales_order_detail, name='sales_order_detail'),
    path('sales-orders/<uuid:pk>/convert_to_invoice/', views_sidecar.convert_order_to_invoice, name='convert_order_to_invoice'),
    
    path('delivery-challans/', views_sidecar.delivery_challan_list_create, name='delivery_challan_list_create'),
    path('delivery-challans/<uuid:pk>/', views_sidecar.delivery_challan_detail, name='delivery_challan_detail'),
    
    path('invoice-settings/', views_sidecar.invoice_settings_view, name='invoice_settings_view'),
    
    # GST Compliance
    path('gst/hsn-summary/', gst_views.hsn_summary_report, name='hsn_summary_report'),
    path('gst/tax-register/', gst_views.tax_register, name='tax_register'),
    path('gst/tax-register/<uuid:invoice_id>/', gst_views.tax_register_invoice_detail, name='tax_register_invoice_detail'),
    path('gst/gstr1-export/', gst_views.gstr1_json_export, name='gstr1_json_export'),
    path('gst/e-invoice/', gst_views.generate_einvoice, name='generate_einvoice'),
    path('gst/e-way-bill/', gst_views.generate_eway_bill, name='generate_eway_bill'),
    
    # Returns (Credit Notes / Debit Notes)
    path('credit-notes/', returns_views.credit_note_list_create, name='credit_note_list_create'),
    path('credit-notes/<uuid:pk>/', returns_views.credit_note_detail, name='credit_note_detail'),
    path('debit-notes/', returns_views.debit_note_list_create, name='debit_note_list_create'),
    path('debit-notes/<uuid:pk>/', returns_views.debit_note_detail, name='debit_note_detail'),
    # Fix endpoints
    path('sales-invoices/recalculate-totals/', views.recalculate_invoice_totals, name='recalculate_invoice_totals'),
]
from django.urls import path
from .views import (
    sales_summary, purchase_summary, inventory_summary, gst_summary, dashboard_summary,
    gstr1_report, stock_summary_report
)

urlpatterns = [
    path('sales-summary/', sales_summary, name='sales_summary'),
    path('purchase-summary/', purchase_summary, name='purchase_summary'),
    path('inventory-summary/', inventory_summary, name='inventory_summary'),
    path('gst-summary/', gst_summary, name='gst_summary'),
    path('dashboard/', dashboard_summary, name='dashboard_summary'),
    path('gstr1-report/', gstr1_report, name='gstr1_report'),
    path('stock-summary/', stock_summary_report, name='stock_summary'),
]
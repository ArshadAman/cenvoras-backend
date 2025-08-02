from django.urls import path
from .views import sales_summary, purchase_summary, inventory_summary, gst_summary, dashboard_summary

urlpatterns = [
    path('sales-summary/', sales_summary, name='sales_summary'),
    path('purchase-summary/', purchase_summary, name='purchase_summary'),
    path('inventory-summary/', inventory_summary, name='inventory_summary'),
    path('gst-summary/', gst_summary, name='gst_summary'),
    path('dashboard/', dashboard_summary, name='dashboard_summary'),
]
from django.urls import path
from . import views

urlpatterns = [
    path('stock-valuation/', views.stock_valuation_view, name='stock-valuation'),
    path('expiry/', views.expiry_report_view, name='expiry-report'),
    path('profit-loss/', views.profit_loss_view, name='profit-loss-report'),
    path('stock-ledger/', views.stock_ledger_view, name='stock-ledger'),
]

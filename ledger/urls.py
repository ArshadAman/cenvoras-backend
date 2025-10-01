from django.urls import path
from .views import client_ledger_list, client_ledger_edit, client_payment_entry, client_ledger_summary
from .accounting_views import (
    chart_of_accounts, journal_entries, general_ledger, 
    trial_balance, setup_default_accounts
)
from .debug_views import test_purchase_bill_accounting, test_sales_invoice_accounting

urlpatterns = [
    # Client Ledger (Existing APIs - unchanged)
    path('client-ledger/', client_ledger_list, name='client_ledger_list'),
    path('client-ledger/<uuid:pk>/', client_ledger_edit, name='client_ledger_edit'),
    path('client-ledger/payment/', client_payment_entry, name='client_payment_entry'),
    path('client-ledger/summary/', client_ledger_summary, name='client_ledger_summary'),
    
    # Double-Entry Accounting APIs (New - optional)
    path('accounts/', chart_of_accounts, name='chart_of_accounts'),
    path('accounts/setup-defaults/', setup_default_accounts, name='setup_default_accounts'),
    path('journal-entries/', journal_entries, name='journal_entries'),
    path('general-ledger/<uuid:account_id>/', general_ledger, name='general_ledger'),
    path('trial-balance/', trial_balance, name='trial_balance'),
    
    # Debug endpoints (remove in production)
    path('debug/test-purchase-bill/', test_purchase_bill_accounting, name='test_purchase_bill'),
    path('debug/test-sales-invoice/', test_sales_invoice_accounting, name='test_sales_invoice'),
]
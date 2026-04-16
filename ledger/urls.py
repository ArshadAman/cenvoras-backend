from django.urls import path
from .accounting_views import (
    chart_of_accounts, account_detail, general_ledger, general_ledger_entries_list, 
    general_ledger_entry_detail, trial_balance, setup_default_accounts,
    create_sales_invoice_ledger_entries, create_purchase_bill_ledger_entries,
    get_ledger_stats
)
from . import financial_views, bank_views, bank_views

urlpatterns = [
    # General Ledger Accounting APIs
    path('accounts/', chart_of_accounts, name='chart_of_accounts'),
    path('accounts/<uuid:account_id>/', account_detail, name='account_detail'),
    path('accounts/stats/', get_ledger_stats, name='get_ledger_stats'),
    path('accounts/setup-defaults/', setup_default_accounts, name='setup_default_accounts'),
    path('general-ledger/<uuid:account_id>/', general_ledger, name='general_ledger'),
    path('general-ledger-entries/', general_ledger_entries_list, name='general_ledger_entries_list'),
    path('general-ledger-entry/<uuid:entry_id>/', general_ledger_entry_detail, name='general_ledger_entry_detail'),
    path('trial-balance/', trial_balance, name='trial_balance'),
    
    # Manual Ledger Entry Creation
    path('create-sales-invoice-entries/', create_sales_invoice_ledger_entries, name='create_sales_invoice_ledger_entries'),
    path('create-purchase-bill-entries/', create_purchase_bill_ledger_entries, name='create_purchase_bill_ledger_entries'),
    
    # Financial Statements
    path('profit-loss/', financial_views.profit_loss_statement, name='profit_loss_statement'),
    path('balance-sheet/', financial_views.balance_sheet, name='balance_sheet'),
    path('balance-sheet/account/<uuid:account_id>/', financial_views.balance_sheet_account_detail, name='balance_sheet_account_detail'),
    path('balance-sheet/diagnostics/', financial_views.balance_sheet_diagnostics, name='balance_sheet_diagnostics'),
    path('cashbook/', financial_views.cashbook, name='cashbook'),
]
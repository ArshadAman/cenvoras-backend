from django.urls import path
from .views import client_ledger_list, client_ledger_edit, client_payment_entry, client_ledger_summary

urlpatterns = [
    path('client-ledger/', client_ledger_list, name='client_ledger_list'),
    path('client-ledger/<uuid:pk>/', client_ledger_edit, name='client_ledger_edit'),
    path('client-ledger/payment/', client_payment_entry, name='client_payment_entry'),
    path('client-ledger/summary/', client_ledger_summary, name='client_ledger_summary'),
]
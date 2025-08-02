import django_filters
from .models import PurchaseBill, SalesInvoice

class PurchaseBillFilter(django_filters.FilterSet):
    bill_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = PurchaseBill
        fields = ['bill_number', 'vendor_name', 'bill_date']

class SalesInvoiceFilter(django_filters.FilterSet):
    invoice_date = django_filters.DateFromToRangeFilter()

    class Meta:
        model = SalesInvoice
        fields = ['invoice_number', 'customer', 'invoice_date']
from django.contrib import admin
from .models import Customer, PurchaseBill, SalesInvoice, SalesInvoiceItem, PurchaseBillItem

admin.site.register(Customer)
admin.site.register(PurchaseBill)
admin.site.register(SalesInvoice)
admin.site.register(SalesInvoiceItem)
admin.site.register(PurchaseBillItem)

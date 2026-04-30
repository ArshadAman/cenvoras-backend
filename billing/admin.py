from django.contrib import admin
from .models import Customer, PurchaseBill, SalesInvoice, SalesInvoiceItem, PurchaseBillItem, PurchaseOrder, PurchaseOrderItem

admin.site.register(Customer)
admin.site.register(PurchaseBill)
admin.site.register(SalesInvoice)
admin.site.register(SalesInvoiceItem)
admin.site.register(PurchaseBillItem)
admin.site.register(PurchaseOrder)
admin.site.register(PurchaseOrderItem)

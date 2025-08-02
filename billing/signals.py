from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from billing.models import PurchaseBillItem, SalesInvoiceItem
from inventory.models import Product

@receiver(post_save, sender=PurchaseBillItem)
def increase_stock_on_purchase(sender, instance, created, **kwargs):
    if created:
        product = instance.product
        product.stock += instance.quantity
        product.save()

@receiver(post_save, sender=SalesInvoiceItem)
def decrease_stock_on_sale(sender, instance, created, **kwargs):
    if created:
        product = instance.product
        product.stock = max(product.stock - instance.quantity, 0)
        product.save()

@receiver(post_delete, sender=PurchaseBillItem)
def decrease_stock_on_purchase_delete(sender, instance, **kwargs):
    product = instance.product
    product.stock = max(product.stock - instance.quantity, 0)
    product.save()

@receiver(post_delete, sender=SalesInvoiceItem)
def increase_stock_on_sale_delete(sender, instance, **kwargs):
    product = instance.product
    product.stock += instance.quantity
    product.save()
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from billing.models import PurchaseBillItem, SalesInvoiceItem, SalesInvoice, PurchaseBill, Payment, Customer
from django.core.exceptions import ValidationError
from inventory.models import Product, Warehouse, StockPoint
from users.models import ActionLog
from django.db.models import F
from django.db.models.functions import Greatest
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# INVENTORY SIGNALS (Atomic)
# ---------------------------------------------------------

@receiver(post_save, sender=PurchaseBillItem)
def increase_stock_on_purchase(sender, instance, created, **kwargs):
    if created:
        total_qty = instance.quantity + instance.free_quantity
        qty_to_add = total_qty
        
        product_id = instance.product_id
        product = Product.objects.only('secondary_unit', 'conversion_factor', 'unit').get(pk=product_id)

        if product.secondary_unit and instance.unit == product.secondary_unit:
            qty_to_add = total_qty * product.conversion_factor
            print(f"DEBUG: Converted purchase qty {total_qty} {instance.unit} to {qty_to_add} {product.unit}")
            
        # ATOMIC UPDATE: Product Stock
        Product.objects.filter(pk=product_id).update(stock=F('stock') + qty_to_add)

        # Update StockPoint
        if instance.batch:
            user = instance.purchase_bill.created_by
            target_warehouse = instance.purchase_bill.warehouse
            
            if not target_warehouse:
                target_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()
                if not target_warehouse:
                    target_warehouse = Warehouse.objects.create(name="Main Warehouse", created_by=user)
            
            stock_point, _ = StockPoint.objects.get_or_create(
                batch=instance.batch,
                warehouse=target_warehouse,
                defaults={'quantity': 0}
            )
            StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') + qty_to_add)
            print(f"DEBUG: Atomically increased stock for batch {instance.batch.batch_number} by {qty_to_add}")

@receiver(post_save, sender=SalesInvoiceItem)
def decrease_stock_on_sale(sender, instance, created, **kwargs):
    if created:
        total_qty = instance.quantity + instance.free_quantity
        qty_to_remove = total_qty
        product_id = instance.product_id
        
        product = Product.objects.only('secondary_unit', 'conversion_factor', 'unit').get(pk=product_id)

        if product.secondary_unit and instance.unit == product.secondary_unit:
            qty_to_remove = total_qty * product.conversion_factor
            print(f"DEBUG: Converted sale qty {total_qty} {instance.unit} to {qty_to_remove} {product.unit}")

        # ATOMIC UPDATE: Product Stock
        Product.objects.filter(pk=product_id).update(stock=Greatest(F('stock') - qty_to_remove, 0))

        # Update StockPoint
        if instance.batch:
            user = instance.sales_invoice.created_by
            target_warehouse = instance.sales_invoice.warehouse
            
            if not target_warehouse:
                target_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()
            
            if target_warehouse:
                stock_point, _ = StockPoint.objects.get_or_create(
                    batch=instance.batch,
                    warehouse=target_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') - qty_to_remove)
                print(f"DEBUG: Atomically decreased stock for batch {instance.batch.batch_number} by {qty_to_remove}")

@receiver(post_save, sender=SalesInvoiceItem)
def update_financials_on_sale_item(sender, instance, created, **kwargs):
    if created and instance.sales_invoice and instance.sales_invoice.customer:
        # Atomic increase: Invoice total
        SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
            total_amount=F('total_amount') + instance.amount
        )
        # Atomic increase: Customer balance (Udhaar)
        Customer.objects.filter(pk=instance.sales_invoice.customer.pk).update(
            current_balance=F('current_balance') + instance.amount
        )
        print(f"DEBUG: Atomically increased Udhaar by {instance.amount} for item {instance.id}")

@receiver(post_delete, sender=SalesInvoiceItem)
def revert_financials_on_sale_item_delete(sender, instance, **kwargs):
    if instance.sales_invoice and instance.sales_invoice.customer:
        # Atomic revert: Invoice total
        SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
            total_amount=Greatest(F('total_amount') - instance.amount, 0)
        )
        # Atomic revert: Customer balance (Udhaar)
        Customer.objects.filter(pk=instance.sales_invoice.customer.pk).update(
            current_balance=F('current_balance') - instance.amount
        )
        print(f"DEBUG: Atomically reverted Udhaar by {instance.amount} for deleted item")

@receiver(post_delete, sender=PurchaseBillItem)
def decrease_stock_on_purchase_delete(sender, instance, **kwargs):
    # ATOMIC REVERT
    Product.objects.filter(pk=instance.product_id).update(stock=Greatest(F('stock') - instance.quantity, 0))
    
    if instance.batch:
        try:
             bill = instance.purchase_bill
             target_warehouse = bill.warehouse
             if not target_warehouse:
                 target_warehouse = Warehouse.objects.filter(created_by=bill.created_by, is_active=True).first()
             
             if target_warehouse:
                 StockPoint.objects.filter(
                     batch=instance.batch, 
                     warehouse=target_warehouse
                 ).update(quantity=Greatest(F('quantity') - instance.quantity, 0))
                 print(f"DEBUG: Atomically reverted stock for batch {instance.batch.batch_number}")
        except Exception as e:
            print(f"ERROR reverting StockPoint on Purchase Delete: {e}")

@receiver(post_delete, sender=SalesInvoiceItem)
def increase_stock_on_sale_delete(sender, instance, **kwargs):
    # ATOMIC REVERT
    Product.objects.filter(pk=instance.product_id).update(stock=F('stock') + instance.quantity)

    if instance.batch:
        try:
             inv = instance.sales_invoice
             target_warehouse = inv.warehouse
             if not target_warehouse:
                 target_warehouse = Warehouse.objects.filter(created_by=inv.created_by, is_active=True).first()
             
             if target_warehouse:
                 StockPoint.objects.filter(
                     batch=instance.batch, 
                     warehouse=target_warehouse
                 ).update(quantity=F('quantity') + instance.quantity)
                 print(f"DEBUG: Atomically restored stock for batch {instance.batch.batch_number}")
        except Exception as e:
            print(f"ERROR reverting StockPoint on Sale Delete: {e}")


# ---------------------------------------------------------
# FINANCIAL SIGNALS (Atomic)
# ---------------------------------------------------------

@receiver(post_save, sender=SalesInvoice)
def update_balance_on_sale(sender, instance, created, **kwargs):
    # Moved to SalesInvoiceItem signals for item-level tracking
    pass

@receiver(post_delete, sender=SalesInvoice)
def revert_balance_on_sale_delete(sender, instance, **kwargs):
    # Handled by SalesInvoiceItem deletions
    pass

@receiver(post_save, sender=Payment)
def update_balance_on_payment(sender, instance, created, **kwargs):
    if created and instance.customer:
        Customer.objects.filter(pk=instance.customer.pk).update(
            current_balance=F('current_balance') - instance.amount
        )
        print(f"DEBUG: Atomically decreased balance by {instance.amount}")

@receiver(post_delete, sender=Payment)
def revert_balance_on_payment_delete(sender, instance, **kwargs):
    if instance.customer:
        Customer.objects.filter(pk=instance.customer.pk).update(
            current_balance=F('current_balance') + instance.amount
        )
        print(f"DEBUG: Reverted balance for deleted payment {instance.pk}")

@receiver(post_save, sender=SalesInvoice)
def check_credit_limit_pre_save(sender, instance, created, **kwargs):
    # Enforced in Serializer
    pass


# ---------------------------------------------------------
# ACCOUNTING / LEDGER SIGNALS
# ---------------------------------------------------------

@receiver(post_save, sender=SalesInvoiceItem)
def create_sales_invoice_accounting_entries(sender, instance, created, **kwargs):
    if created:
        sales_invoice = instance.sales_invoice
        from ledger.models import GeneralLedgerEntry
        
        existing_entries = GeneralLedgerEntry.objects.filter(sales_invoice=sales_invoice)
        if existing_entries.exists():
            existing_entries.delete()
        
        try:
            from ledger.services import AccountingService
            AccountingService.create_sales_invoice_entries(sales_invoice)
        except Exception:
            pass

@receiver(post_save, sender=SalesInvoice)
def create_sales_invoice_accounting_entries_fallback(sender, instance, created, **kwargs):
    if created:
        pass 

@receiver(post_save, sender=PurchaseBillItem)
def create_purchase_bill_accounting_entries(sender, instance, created, **kwargs):
    if created:
        purchase_bill = instance.purchase_bill
        from ledger.models import GeneralLedgerEntry
        
        existing_entries = GeneralLedgerEntry.objects.filter(purchase_bill=purchase_bill)
        if existing_entries.exists():
            existing_entries.delete()
        
        try:
            from ledger.services import AccountingService
            AccountingService.create_purchase_bill_entries(purchase_bill)
        except Exception:
            pass

@receiver(post_save, sender=PurchaseBill)  
def create_purchase_bill_accounting_entries_fallback(sender, instance, created, **kwargs):
    if created:
        from django.db import transaction
        def create_entries_after_commit():
            from billing.models import PurchaseBillItem
            from ledger.models import GeneralLedgerEntry
            line_items = PurchaseBillItem.objects.filter(purchase_bill=instance)
            existing_entries = GeneralLedgerEntry.objects.filter(purchase_bill=instance)
            if not line_items.exists() and not existing_entries.exists():
                try:
                    from ledger.services import AccountingService
                    AccountingService.create_purchase_bill_entries(instance)
                except Exception:
                    pass
        transaction.on_commit(create_entries_after_commit)
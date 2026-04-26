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
    if created and instance.sales_invoice:
        # Atomic increase: Invoice total remains item-driven.
        SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
            total_amount=F('total_amount') + instance.amount
        )

@receiver(post_delete, sender=SalesInvoiceItem)
def revert_financials_on_sale_item_delete(sender, instance, **kwargs):
    if instance.sales_invoice:
        # Atomic revert: Invoice total
        SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
            total_amount=Greatest(F('total_amount') - instance.amount, 0)
        )

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
        if instance.invoice_id and getattr(instance.invoice, 'status', None) == 'draft':
            print(f"DEBUG: Payment {instance.pk} ignored for draft invoice {instance.invoice_id}")
            return

        Customer.objects.filter(pk=instance.customer.pk).update(
            current_balance=F('current_balance') - instance.amount
        )
        print(f"DEBUG: Payment {instance.pk} - Decreased customer balance by {instance.amount}")

        if instance.invoice_id:
            print(f"DEBUG: Payment {instance.pk} - Linking to invoice {instance.invoice_id}")
            SalesInvoice.objects.filter(pk=instance.invoice_id).update(
                amount_paid=F('amount_paid') + instance.amount
            )
            invoice = SalesInvoice.objects.get(pk=instance.invoice_id)
            old_status = invoice.payment_status
            invoice.refresh_payment_status(save=True)
            print(f"DEBUG: Invoice {instance.invoice_id} status: {old_status} → {invoice.payment_status}")
        else:
            print(f"DEBUG: Payment {instance.pk} - NO INVOICE LINKED (status won't update)")
        
        # Create ledger entries
        try:
            from ledger.services import AccountingService
            AccountingService.create_payment_received_entries(
                customer=instance.customer,
                amount=instance.amount,
                description=instance.notes or f"Payment received - {instance.reference or ''}",
                date=instance.date,
                user=instance.created_by,
                invoice=instance.invoice,
                payment_id=instance.id,
            )
        except Exception as e:
            print(f"ERROR creating ledger entries for payment: {e}")

@receiver(post_delete, sender=Payment)
def revert_balance_on_payment_delete(sender, instance, **kwargs):
    if instance.customer:
        if instance.invoice_id and getattr(instance.invoice, 'status', None) == 'draft':
            print(f"DEBUG: Payment deletion ignored for draft invoice {instance.invoice_id}")
            return

        Customer.objects.filter(pk=instance.customer.pk).update(
            current_balance=F('current_balance') + instance.amount
        )
        print(f"DEBUG: Payment deletion - Reverted customer balance by {instance.amount}")

        if instance.invoice_id:
            print(f"DEBUG: Payment deletion - Reverting invoice {instance.invoice_id}")
            SalesInvoice.objects.filter(pk=instance.invoice_id).update(
                amount_paid=Greatest(F('amount_paid') - instance.amount, 0)
            )
            invoice = SalesInvoice.objects.get(pk=instance.invoice_id)
            old_status = invoice.payment_status
            invoice.refresh_payment_status(save=True)
            print(f"DEBUG: Invoice {instance.invoice_id} status: {old_status} → {invoice.payment_status}")

@receiver(post_save, sender=SalesInvoice)
def check_credit_limit_pre_save(sender, instance, created, **kwargs):
    # Enforced in Serializer
    pass


# ---------------------------------------------------------
# ACCOUNTING / LEDGER SIGNALS
# ---------------------------------------------------------

@receiver(post_save, sender=SalesInvoiceItem)
def create_sales_invoice_accounting_entries(sender, instance, created, **kwargs):
    # Ledger rebuild is now done once per invoice save in the serializer.
    return

@receiver(post_save, sender=SalesInvoice)
def create_sales_invoice_accounting_entries_fallback(sender, instance, created, **kwargs):
    if created:
        pass 

@receiver(post_save, sender=PurchaseBillItem)
def create_purchase_bill_accounting_entries(sender, instance, created, **kwargs):
    # Ledger rebuild is now done once per bill save in the serializer.
    return

@receiver(post_save, sender=PurchaseBill)  
def create_purchase_bill_accounting_entries_fallback(sender, instance, created, **kwargs):
    return
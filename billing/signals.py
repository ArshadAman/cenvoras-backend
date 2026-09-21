from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from billing.models import PurchaseBillItem, SalesInvoiceItem, SalesInvoice, PurchaseBill, Payment, Customer
from django.core.exceptions import ValidationError
from inventory.models import Product, Warehouse, StockPoint
from users.models import ActionLog
from django.db.models import F
from django.db.models.functions import Greatest
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# INVENTORY SIGNALS (Atomic & Delta-Aware)
# ---------------------------------------------------------

def _get_item_effective_qty(instance):
    """
    Calculate total effective quantity in primary product units,
    taking into account quantity, free_quantity, and secondary unit conversion.
    """
    total_qty = (instance.quantity or 0) + (instance.free_quantity or 0)
    product_id = instance.product_id
    if not product_id:
        return total_qty

    try:
        product = Product.objects.only('secondary_unit', 'conversion_factor', 'unit').get(pk=product_id)
        if product.secondary_unit and instance.unit == product.secondary_unit:
            return total_qty * (product.conversion_factor or 1)
    except Product.DoesNotExist:
        pass
    return total_qty


def _get_target_warehouse(parent_doc, user):
    target_wh = getattr(parent_doc, 'warehouse', None)
    if not target_wh:
        target_wh = Warehouse.objects.filter(created_by=user, is_active=True).first()
        if not target_wh:
            target_wh = Warehouse.objects.create(name="Main Warehouse", created_by=user)
    return target_wh


@receiver(pre_save, sender=PurchaseBillItem)
def track_purchase_item_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = PurchaseBillItem.objects.select_related('purchase_bill').get(pk=instance.pk)
            instance._old_state = {
                'product_id': old.product_id,
                'batch_id': old.batch_id,
                'effective_qty': _get_item_effective_qty(old),
                'amount': old.amount or 0,
                'warehouse_id': old.purchase_bill.warehouse_id if old.purchase_bill else None,
            }
        except PurchaseBillItem.DoesNotExist:
            instance._old_state = None
    else:
        instance._old_state = None


@receiver(post_save, sender=PurchaseBillItem)
def increase_stock_on_purchase(sender, instance, created, **kwargs):
    qty_to_add = _get_item_effective_qty(instance)
    product_id = instance.product_id
    user = getattr(instance.purchase_bill, 'created_by', None)
    target_warehouse = _get_target_warehouse(instance.purchase_bill, user) if user else None
    old_state = getattr(instance, '_old_state', None)

    if created or not old_state:
        # ATOMIC UPDATE: Increase Product Stock
        Product.objects.filter(pk=product_id).update(stock=F('stock') + qty_to_add)

        # Update StockPoint
        if instance.batch and target_warehouse:
            stock_point, _ = StockPoint.objects.get_or_create(
                batch=instance.batch,
                warehouse=target_warehouse,
                defaults={'quantity': 0}
            )
            StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') + qty_to_add)
    else:
        old_product_id = old_state['product_id']
        old_batch_id = old_state['batch_id']
        old_qty = old_state['effective_qty']

        if old_product_id == product_id and old_batch_id == instance.batch_id:
            delta = qty_to_add - old_qty
            if delta != 0:
                Product.objects.filter(pk=product_id).update(stock=F('stock') + delta)
                if instance.batch and target_warehouse:
                    stock_point, _ = StockPoint.objects.get_or_create(
                        batch=instance.batch,
                        warehouse=target_warehouse,
                        defaults={'quantity': 0}
                    )
                    StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') + delta)
        else:
            # Revert old
            Product.objects.filter(pk=old_product_id).update(stock=F('stock') - old_qty)
            if old_batch_id and target_warehouse:
                StockPoint.objects.filter(batch_id=old_batch_id, warehouse=target_warehouse).update(
                    quantity=Greatest(F('quantity') - old_qty, 0)
                )
            # Add new
            Product.objects.filter(pk=product_id).update(stock=F('stock') + qty_to_add)
            if instance.batch and target_warehouse:
                stock_point, _ = StockPoint.objects.get_or_create(
                    batch=instance.batch,
                    warehouse=target_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') + qty_to_add)


@receiver(pre_save, sender=SalesInvoiceItem)
def track_sale_item_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = SalesInvoiceItem.objects.select_related('sales_invoice').get(pk=instance.pk)
            instance._old_state = {
                'product_id': old.product_id,
                'batch_id': old.batch_id,
                'effective_qty': _get_item_effective_qty(old),
                'amount': old.amount or 0,
                'warehouse_id': old.sales_invoice.warehouse_id if old.sales_invoice else None,
            }
        except SalesInvoiceItem.DoesNotExist:
            instance._old_state = None
    else:
        instance._old_state = None


@receiver(post_save, sender=SalesInvoiceItem)
def decrease_stock_on_sale(sender, instance, created, **kwargs):
    qty_to_remove = _get_item_effective_qty(instance)
    product_id = instance.product_id
    user = getattr(instance.sales_invoice, 'created_by', None)
    target_warehouse = _get_target_warehouse(instance.sales_invoice, user) if user else None
    old_state = getattr(instance, '_old_state', None)

    if created or not old_state:
        # ATOMIC UPDATE: Decrease Product Stock
        Product.objects.filter(pk=product_id).update(stock=F('stock') - qty_to_remove)

        # Update StockPoint
        if instance.batch and target_warehouse:
            stock_point, _ = StockPoint.objects.get_or_create(
                batch=instance.batch,
                warehouse=target_warehouse,
                defaults={'quantity': 0}
            )
            StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') - qty_to_remove)
    else:
        old_product_id = old_state['product_id']
        old_batch_id = old_state['batch_id']
        old_qty = old_state['effective_qty']

        if old_product_id == product_id and old_batch_id == instance.batch_id:
            delta = qty_to_remove - old_qty
            if delta != 0:
                Product.objects.filter(pk=product_id).update(stock=F('stock') - delta)
                if instance.batch and target_warehouse:
                    stock_point, _ = StockPoint.objects.get_or_create(
                        batch=instance.batch,
                        warehouse=target_warehouse,
                        defaults={'quantity': 0}
                    )
                    StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') - delta)
        else:
            # Restore old
            Product.objects.filter(pk=old_product_id).update(stock=F('stock') + old_qty)
            if old_batch_id and target_warehouse:
                StockPoint.objects.filter(batch_id=old_batch_id, warehouse=target_warehouse).update(
                    quantity=F('quantity') + old_qty
                )
            # Deduct new
            Product.objects.filter(pk=product_id).update(stock=F('stock') - qty_to_remove)
            if instance.batch and target_warehouse:
                stock_point, _ = StockPoint.objects.get_or_create(
                    batch=instance.batch,
                    warehouse=target_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=stock_point.pk).update(quantity=F('quantity') - qty_to_remove)


@receiver(post_save, sender=SalesInvoiceItem)
def update_financials_on_sale_item(sender, instance, created, **kwargs):
    if instance.sales_invoice:
        old_state = getattr(instance, '_old_state', None)
        if created or not old_state:
            SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
                total_amount=F('total_amount') + instance.amount
            )
        else:
            old_amount = old_state.get('amount', 0)
            diff = instance.amount - old_amount
            if diff != 0:
                SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
                    total_amount=F('total_amount') + diff
                )


@receiver(post_delete, sender=SalesInvoiceItem)
def revert_financials_on_sale_item_delete(sender, instance, **kwargs):
    if instance.sales_invoice:
        SalesInvoice.objects.filter(pk=instance.sales_invoice.pk).update(
            total_amount=Greatest(F('total_amount') - instance.amount, 0)
        )


@receiver(post_delete, sender=PurchaseBillItem)
def decrease_stock_on_purchase_delete(sender, instance, **kwargs):
    qty_to_revert = _get_item_effective_qty(instance)
    Product.objects.filter(pk=instance.product_id).update(stock=F('stock') - qty_to_revert)
    
    if instance.batch and instance.purchase_bill:
        try:
            bill = instance.purchase_bill
            target_warehouse = _get_target_warehouse(bill, bill.created_by)
            if target_warehouse:
                StockPoint.objects.filter(
                    batch=instance.batch, 
                    warehouse=target_warehouse
                ).update(quantity=Greatest(F('quantity') - qty_to_revert, 0))
        except Exception as e:
            logger.error("Error reverting StockPoint on Purchase Delete: %s", e)


@receiver(post_delete, sender=SalesInvoiceItem)
def increase_stock_on_sale_delete(sender, instance, **kwargs):
    qty_to_restore = _get_item_effective_qty(instance)
    Product.objects.filter(pk=instance.product_id).update(stock=F('stock') + qty_to_restore)

    if instance.batch and instance.sales_invoice:
        try:
            inv = instance.sales_invoice
            target_warehouse = _get_target_warehouse(inv, inv.created_by)
            if target_warehouse:
                StockPoint.objects.filter(
                    batch=instance.batch, 
                    warehouse=target_warehouse
                ).update(quantity=F('quantity') + qty_to_restore)
        except Exception as e:
            logger.error("Error reverting StockPoint on Sale Delete: %s", e)


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

@receiver(pre_save, sender=Payment)
def track_payment_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = Payment.objects.get(pk=instance.pk)
            instance._old_state = {
                'customer_id': old.customer_id,
                'invoice_id': old.invoice_id,
                'amount': old.amount or Decimal('0.00'),
                'mode': old.mode,
                'date': old.date,
                'reference': old.reference,
                'notes': old.notes,
            }
        except Payment.DoesNotExist:
            instance._old_state = None
    else:
        instance._old_state = None


@receiver(post_save, sender=Payment)
def update_balance_on_payment(sender, instance, created, **kwargs):
    if instance.invoice_id and getattr(instance.invoice, 'status', None) == 'draft':
        logger.debug(f"Payment {instance.pk} ignored for draft invoice {instance.invoice_id}")
        return

    old_state = getattr(instance, '_old_state', None)

    if created or not old_state:
        # --- NEW PAYMENT CREATED ---
        if instance.customer_id:
            Customer.objects.filter(pk=instance.customer_id).update(
                current_balance=F('current_balance') - instance.amount
            )
            logger.debug(f"Payment {instance.pk} - Decreased customer {instance.customer_id} balance by {instance.amount}")

        if instance.invoice_id:
            SalesInvoice.objects.filter(pk=instance.invoice_id).update(
                amount_paid=F('amount_paid') + instance.amount
            )
            invoice = SalesInvoice.objects.get(pk=instance.invoice_id)
            invoice.refresh_payment_status(save=True)
            logger.debug(f"Invoice {instance.invoice_id} status updated to {invoice.payment_status}")

        # Create ledger entries
        try:
            from ledger.services import AccountingService
            AccountingService.create_payment_received_entries(
                customer=instance.customer,
                amount=instance.amount,
                description=instance.notes or f"Payment received ({instance.get_mode_display()}) - {instance.reference or ''}".strip(' -'),
                date=instance.date,
                user=instance.created_by,
                invoice=instance.invoice,
                payment_id=instance.id,
                payment_mode=instance.mode,
            )
        except Exception as e:
            logger.error(f"Error creating ledger entries for payment: {e}")

    else:
        # --- EXISTING PAYMENT EDITED ---
        old_customer_id = old_state.get('customer_id')
        new_customer_id = instance.customer_id
        old_invoice_id = old_state.get('invoice_id')
        new_invoice_id = instance.invoice_id
        old_amount = Decimal(str(old_state.get('amount') or 0))
        new_amount = Decimal(str(instance.amount or 0))

        # 1. Update Customer Balance
        if old_customer_id == new_customer_id:
            delta = new_amount - old_amount
            if delta != 0 and new_customer_id:
                Customer.objects.filter(pk=new_customer_id).update(
                    current_balance=F('current_balance') - delta
                )
                logger.debug(f"Payment {instance.pk} edit - Customer {new_customer_id} balance adjusted by {-delta}")
        else:
            if old_customer_id:
                Customer.objects.filter(pk=old_customer_id).update(
                    current_balance=F('current_balance') + old_amount
                )
            if new_customer_id:
                Customer.objects.filter(pk=new_customer_id).update(
                    current_balance=F('current_balance') - new_amount
                )
            logger.debug(f"Payment {instance.pk} edit - Swapped customers: {old_customer_id} -> {new_customer_id}")

        # 2. Update Invoice Amount Paid and Payment Status
        if old_invoice_id == new_invoice_id:
            if new_invoice_id:
                delta = new_amount - old_amount
                if delta != 0:
                    SalesInvoice.objects.filter(pk=new_invoice_id).update(
                        amount_paid=Greatest(F('amount_paid') + delta, Decimal('0.00'))
                    )
                    invoice = SalesInvoice.objects.get(pk=new_invoice_id)
                    invoice.refresh_payment_status(save=True)
                    logger.debug(f"Invoice {new_invoice_id} status on edit: {invoice.payment_status}")
        else:
            if old_invoice_id:
                SalesInvoice.objects.filter(pk=old_invoice_id).update(
                    amount_paid=Greatest(F('amount_paid') - old_amount, Decimal('0.00'))
                )
                try:
                    old_inv = SalesInvoice.objects.get(pk=old_invoice_id)
                    old_inv.refresh_payment_status(save=True)
                except SalesInvoice.DoesNotExist:
                    pass

            if new_invoice_id:
                SalesInvoice.objects.filter(pk=new_invoice_id).update(
                    amount_paid=F('amount_paid') + new_amount
                )
                try:
                    new_inv = SalesInvoice.objects.get(pk=new_invoice_id)
                    new_inv.refresh_payment_status(save=True)
                except SalesInvoice.DoesNotExist:
                    pass
            logger.debug(f"Payment {instance.pk} edit - Swapped invoices: {old_invoice_id} -> {new_invoice_id}")

        # 3. Rebuild General Ledger Entries for this payment
        try:
            from ledger.models import GeneralLedgerEntry
            from ledger.services import AccountingService
            GeneralLedgerEntry.objects.filter(reference=f"Payment Received {instance.id}").delete()
            AccountingService.create_payment_received_entries(
                customer=instance.customer,
                amount=instance.amount,
                description=instance.notes or f"Payment received ({instance.get_mode_display()}) - {instance.reference or ''}".strip(' -'),
                date=instance.date,
                user=instance.created_by,
                invoice=instance.invoice,
                payment_id=instance.id,
                payment_mode=instance.mode,
            )
            logger.debug(f"Rebuilt ledger entries for edited payment {instance.id}")
        except Exception as e:
            logger.error(f"Error updating ledger entries for payment edit: {e}")


@receiver(post_delete, sender=Payment)
def revert_balance_on_payment_delete(sender, instance, **kwargs):
    if instance.customer_id:
        if instance.invoice_id and getattr(instance.invoice, 'status', None) == 'draft':
            logger.debug(f"Payment deletion ignored for draft invoice {instance.invoice_id}")
            return

        Customer.objects.filter(pk=instance.customer_id).update(
            current_balance=F('current_balance') + instance.amount
        )
        logger.debug(f"Payment deletion - Reverted customer balance by {instance.amount}")

        if instance.invoice_id:
            SalesInvoice.objects.filter(pk=instance.invoice_id).update(
                amount_paid=Greatest(F('amount_paid') - instance.amount, Decimal('0.00'))
            )
            try:
                invoice = SalesInvoice.objects.get(pk=instance.invoice_id)
                invoice.refresh_payment_status(save=True)
                logger.debug(f"Invoice {instance.invoice_id} status on delete: {invoice.payment_status}")
            except SalesInvoice.DoesNotExist:
                pass

        # Clean up General Ledger entries
        try:
            from ledger.models import GeneralLedgerEntry
            GeneralLedgerEntry.objects.filter(reference=f"Payment Received {instance.id}").delete()
            logger.debug(f"Deleted ledger entries for deleted payment {instance.id}")
        except Exception as e:
            logger.error(f"Error deleting ledger entries for payment delete: {e}")

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
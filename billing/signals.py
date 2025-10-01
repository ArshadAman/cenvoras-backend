from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from billing.models import PurchaseBillItem, SalesInvoiceItem, SalesInvoice, PurchaseBill
from inventory.models import Product
import logging

logger = logging.getLogger(__name__)

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


# Accounting Signals for Double-Entry Bookkeeping

@receiver(post_save, sender=SalesInvoiceItem)
def create_sales_invoice_accounting_entries(sender, instance, created, **kwargs):
    """
    Create or recreate accounting entries when sales invoice items are created
    This ensures detailed line item information is captured
    """
    if created:
        sales_invoice = instance.sales_invoice
        
        print(f"DEBUG: Line item added to Sales Invoice {sales_invoice.invoice_number}")
        
        # Always recreate entries to ensure they include all line items
        from ledger.models import GeneralLedgerEntry
        
        # Delete any existing entries for this invoice
        existing_entries = GeneralLedgerEntry.objects.filter(sales_invoice=sales_invoice)
        if existing_entries.exists():
            print(f"DEBUG: Deleting {existing_entries.count()} existing entries to recreate with line items")
            existing_entries.delete()
        
        try:
            from ledger.services import AccountingService
            logger.info(f"Creating detailed accounting entries for Sales Invoice {sales_invoice.invoice_number}")
            success = AccountingService.create_sales_invoice_entries(sales_invoice)
            logger.info(f"Successfully created general ledger entries for Sales Invoice {sales_invoice.invoice_number}")
            print(f"DEBUG: Successfully created detailed accounting entries - {success}")
        except Exception as e:
            error_msg = f"Failed to create accounting entries for Sales Invoice {sales_invoice.invoice_number}: {str(e)}"
            logger.error(error_msg)
            print(f"DEBUG ERROR: {error_msg}")
            import traceback
            print(f"DEBUG TRACEBACK: {traceback.format_exc()}")
            # Don't raise exception to avoid breaking invoice creation
            pass


# Fallback signal - only creates entries if no line items are created within a reasonable time
@receiver(post_save, sender=SalesInvoice)
def create_sales_invoice_accounting_entries_fallback(sender, instance, created, **kwargs):
    """
    Fallback: Create accounting entries for sales invoices 
    This will be overridden by the line item signal if items are added
    """
    if created:
        print(f"DEBUG: SalesInvoice created: {instance.invoice_number}, will check for entries later")


@receiver(post_save, sender=PurchaseBillItem)
def create_purchase_bill_accounting_entries(sender, instance, created, **kwargs):
    """
    Create or recreate accounting entries when purchase bill items are created
    This ensures detailed line item information is captured
    """
    if created:
        purchase_bill = instance.purchase_bill
        
        print(f"DEBUG: Line item added to Purchase Bill {purchase_bill.bill_number}")
        
        # Always recreate entries to ensure they include all line items
        from ledger.models import GeneralLedgerEntry
        
        # Delete any existing entries for this bill
        existing_entries = GeneralLedgerEntry.objects.filter(purchase_bill=purchase_bill)
        if existing_entries.exists():
            print(f"DEBUG: Deleting {existing_entries.count()} existing entries to recreate with line items")
            existing_entries.delete()
        
        try:
            from ledger.services import AccountingService
            logger.info(f"Creating detailed accounting entries for Purchase Bill {purchase_bill.bill_number}")
            success = AccountingService.create_purchase_bill_entries(purchase_bill)
            logger.info(f"Successfully created general ledger entries for Purchase Bill {purchase_bill.bill_number}")
            print(f"DEBUG: Successfully created detailed accounting entries - {success}")
        except Exception as e:
            error_msg = f"Failed to create accounting entries for Purchase Bill {purchase_bill.bill_number}: {str(e)}"
            logger.error(error_msg)
            print(f"DEBUG ERROR: {error_msg}")
            import traceback
            print(f"DEBUG TRACEBACK: {traceback.format_exc()}")
            # Don't raise exception to avoid breaking bill creation
            pass


# Keep the original signal as a fallback for bills without line items
@receiver(post_save, sender=PurchaseBill)  
def create_purchase_bill_accounting_entries_fallback(sender, instance, created, **kwargs):
    """
    Fallback: Create accounting entries for purchase bills without line items
    """
    if created:
        from django.db import transaction
        
        def create_entries_after_commit():
            from billing.models import PurchaseBillItem
            from ledger.models import GeneralLedgerEntry
            
            # Check if line items exist
            line_items = PurchaseBillItem.objects.filter(purchase_bill=instance)
            existing_entries = GeneralLedgerEntry.objects.filter(purchase_bill=instance)
            
            # Only create if no line items and no existing entries
            if not line_items.exists() and not existing_entries.exists():
                print(f"DEBUG: Creating fallback accounting entries for Purchase Bill {instance.bill_number}")
                
                try:
                    from ledger.services import AccountingService
                    success = AccountingService.create_purchase_bill_entries(instance)
                    print(f"DEBUG: Fallback accounting entries created - {success}")
                except Exception as e:
                    print(f"DEBUG ERROR in fallback: {e}")
        
        # Schedule after the current transaction commits
        transaction.on_commit(create_entries_after_commit)
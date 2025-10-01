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

@receiver(post_save, sender=SalesInvoice)
def create_sales_invoice_accounting_entries(sender, instance, created, **kwargs):
    """
    Automatically create double-entry accounting entries when a sales invoice is created
    
    This creates:
    1. Journal Entry with balanced debits and credits
    2. General Ledger entries (Dr. Accounts Receivable, Cr. Sales Revenue)  
    3. Client Ledger entry (subsidiary ledger for customer tracking)
    """
    print(f"DEBUG: SalesInvoice signal triggered - created: {created}, invoice: {instance.invoice_number}")
    
    if created:
        try:
            from ledger.services import AccountingService
            print(f"DEBUG: Imported AccountingService, creating entries for {instance.invoice_number}")
            logger.info(f"Creating accounting entries for Sales Invoice {instance.invoice_number}")
            journal_entry = AccountingService.create_sales_invoice_entries(instance)
            logger.info(f"Successfully created journal entry {journal_entry.id} for Sales Invoice {instance.invoice_number}")
            print(f"DEBUG: Successfully created journal entry {journal_entry.id}")
        except Exception as e:
            error_msg = f"Failed to create accounting entries for Sales Invoice {instance.invoice_number}: {str(e)}"
            logger.error(error_msg)
            print(f"DEBUG ERROR: {error_msg}")
            import traceback
            print(f"DEBUG TRACEBACK: {traceback.format_exc()}")
            # Don't raise exception to avoid breaking invoice creation
            pass


@receiver(post_save, sender=PurchaseBill)  
def create_purchase_bill_accounting_entries(sender, instance, created, **kwargs):
    """
    Automatically create double-entry accounting entries when a purchase bill is created
    
    This creates:
    1. Journal Entry with balanced debits and credits
    2. General Ledger entries (Dr. Purchases, Cr. Accounts Payable)
    """
    print(f"DEBUG: PurchaseBill signal triggered - created: {created}, bill: {getattr(instance, 'bill_number', 'NO_BILL_NUMBER')}")
    print(f"DEBUG: PurchaseBill fields - vendor: {getattr(instance, 'vendor_name', 'NO_VENDOR')}, total: {getattr(instance, 'total_amount', 'NO_TOTAL')}")
    print(f"DEBUG: PurchaseBill user: {getattr(instance, 'created_by', 'NO_USER')}")
    
    if created:
        print(f"DEBUG: Starting accounting entry creation for PurchaseBill")
        try:
            # Test if we can access the required fields
            required_fields = ['bill_date', 'vendor_name', 'bill_number', 'total_amount', 'created_by']
            for field in required_fields:
                value = getattr(instance, field, 'MISSING')
                print(f"DEBUG: Field {field} = {value}")
            
            from ledger.services import AccountingService
            print(f"DEBUG: Imported AccountingService successfully")
            
            logger.info(f"Creating accounting entries for Purchase Bill {instance.bill_number}")
            journal_entry = AccountingService.create_purchase_bill_entries(instance)
            logger.info(f"Successfully created journal entry {journal_entry.id} for Purchase Bill {instance.bill_number}")
            print(f"DEBUG: SUCCESS - Created journal entry {journal_entry.id}")
            
        except Exception as e:
            error_msg = f"Failed to create accounting entries for Purchase Bill {getattr(instance, 'bill_number', 'UNKNOWN')}: {str(e)}"
            logger.error(error_msg)
            print(f"DEBUG ERROR: {error_msg}")
            import traceback
            traceback.print_exc()
            # Don't raise exception to avoid breaking bill creation
            pass
    else:
        print(f"DEBUG: PurchaseBill signal fired but created=False (update operation)")
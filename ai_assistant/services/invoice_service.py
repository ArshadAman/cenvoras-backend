from django.db.models import Q
from django.utils import timezone
from billing.models import SalesInvoice, Customer
from inventory.models import Product
from billing.serializers import SalesInvoiceSerializer
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

def search_customers(user, query):
    tenant = getattr(user, 'active_tenant', user)
    return Customer.objects.filter(
        Q(name__icontains=query) | Q(phone__icontains=query),
        created_by=tenant
    )[:5]

def search_products(user, query):
    tenant = getattr(user, 'active_tenant', user)
    return Product.objects.filter(
        name__icontains=query,
        created_by=tenant
    )[:5]


def get_next_invoice_number_internal(user, prefix='INV-'):
    tenant = getattr(user, 'active_tenant', user)
    tenant_id = str(tenant.id)[:4].upper()
    full_prefix = f'{prefix}{tenant_id}-'

    invoices = SalesInvoice.objects.filter(
        created_by=tenant,
        invoice_number__startswith=full_prefix,
    )

    max_num = 0
    for inv in invoices:
        suffix = inv.invoice_number.replace(full_prefix, '')
        try:
            num = int(suffix)
            if num > max_num:
                max_num = num
        except ValueError:
            continue

    next_num = max_num + 1
    return f"{full_prefix}{next_num:03d}"

def create_invoice_from_ai(user, entities, request=None):
    """
    Directly creates an invoice from AI extracted entities.
    Handles customer/product creation via SalesInvoiceSerializer.
    """
    tenant = getattr(user, 'active_tenant', user)
    
    data = entities.copy()
    
    # Ensure invoice number
    if not data.get('invoice_number'):
        profile = getattr(user, 'profile', None)
        prefix = getattr(profile, 'invoice_prefix', 'INV-') or 'INV-'
        data['invoice_number'] = get_next_invoice_number_internal(user, prefix)

    
    # Ensure date
    if not data.get('invoice_date'):
        data['invoice_date'] = timezone.now().date().isoformat()
    
    # Set status to final for direct creation
    data['status'] = 'final'
    
    # Clean items
    if 'items' in data:
        for item in data['items']:
            if item.get('price') is None:
                item['price'] = 0
            if item.get('quantity') is None:
                item['quantity'] = 1
                
    try:
        serializer = SalesInvoiceSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            invoice = serializer.save(created_by=tenant)
            return {
                "status": "success",
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "customer_name": invoice.customer_name,
                "total_amount": round(float(invoice.total_amount), 2)
            }

        else:
            logger.warning(f"AI Invoice Validation Failed: {serializer.errors}")
            return {
                "status": "error",
                "errors": serializer.errors,
                "entities": entities # Fallback to draft
            }
    except Exception as e:
        logger.error(f"AI Invoice Creation Exception: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "entities": entities
        }

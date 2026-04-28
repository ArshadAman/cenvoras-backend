import os
import django
import sys
from decimal import Decimal

# Set up Django environment
sys.path.append('/Users/arshadaman/Cenvoras/cenvoras')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from billing.models import SalesInvoice, SalesInvoiceItem
from django.db.models import Sum

# Get all invoices
invoices = SalesInvoice.objects.all().order_by('invoice_date')

print(f"{'Number':<15} | {'Date':<12} | {'Amount(DB)':<10} | {'Sum(Items)':<10} | {'Count(Items)'}")
print("-" * 70)
for inv in invoices:
    items_sum = inv.items.aggregate(total=Sum('amount'))['total'] or 0
    items_count = inv.items.count()
    print(f"{inv.invoice_number:<15} | {str(inv.invoice_date):<12} | {inv.total_amount:<10} | {items_sum:<10} | {items_count}")

total_final = SalesInvoice.objects.filter(status='final').aggregate(Sum('total_amount'))['total_amount__sum'] or 0
print("-" * 70)
print(f"Total Final Sales (DB Field): {total_final}")

# Check for duplicates in Sum
raw_sum = SalesInvoice.objects.filter(status='final').aggregate(total=Sum('total_amount'))['total'] or 0
print(f"Direct Aggregate Sum: {raw_sum}")

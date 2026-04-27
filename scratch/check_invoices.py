import os
import django
import sys

# Set up Django environment
sys.path.append('/Users/arshadaman/Cenvoras/cenvoras')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from billing.models import SalesInvoice
from django.db.models import Sum

# Get all invoices
invoices = SalesInvoice.objects.all().order_by('invoice_date')

print(f"{'ID':<40} | {'Number':<15} | {'Date':<12} | {'Amount':<10} | {'Status':<10} | {'Created By'}")
print("-" * 110)
for inv in invoices:
    print(f"{str(inv.id):<40} | {inv.invoice_number:<15} | {str(inv.invoice_date):<12} | {inv.total_amount:<10} | {inv.status:<10} | {inv.created_by.email}")

total_final = SalesInvoice.objects.filter(status='final').aggregate(Sum('total_amount'))['total_amount__sum'] or 0
print("-" * 110)
print(f"Total Final Sales: {total_final}")

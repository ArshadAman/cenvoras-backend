import os
import django
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from billing.models import SalesInvoice
from users.models import User

# Find the user mearshadaman@gmail.com
user = User.objects.get(email='mearshadaman@gmail.com')
tenant = user.active_tenant or user

print(f"Tenant: {tenant.email} (ID: {tenant.id})")

# Find all invoices for this tenant and their team
from django.db.models import Q
invoices = SalesInvoice.objects.filter(
    Q(created_by=tenant) | Q(created_by__parent=tenant)
).exclude(status='draft').order_by('-invoice_date', '-created_at')

print(f"Total Final Invoices: {len(invoices)}")
total_sum = Decimal('0')
for inv in invoices:
    print(f"ID: {inv.id} | Date: {inv.invoice_date} | Created At: {inv.created_at} | Total: {inv.total_amount} | Created By: {inv.created_by.email}")
    total_sum += inv.total_amount

print(f"Overall Total: {total_sum}")

# Today's invoices (Apr 28)
today_invoices = invoices.filter(invoice_date='2026-04-28')
print(f"Invoices on 2026-04-28: {len(today_invoices)} | Sum: {sum(i.total_amount for i in today_invoices)}")

# Yesterday's invoices (Apr 27)
yesterday_invoices = invoices.filter(invoice_date='2026-04-27')
print(f"Invoices on 2026-04-27: {len(yesterday_invoices)} | Sum: {sum(i.total_amount for i in yesterday_invoices)}")

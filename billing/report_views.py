from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import SalesInvoice, Customer
from .serializers import SalesInvoiceSerializer
from django.utils import timezone
import datetime

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def overdue_bills_report(request):
    """
    Get a list of overdue bills (Due Date < Today AND Balance > 0).
    For now, since we don't track per-invoice balance (only customer global balance),
    we can return invoices that are past due date for customers who have a positive balance.
    
    Ideally, we should track 'amount_paid' on each invoice to know if *that specific* invoice is unpaid.
    Gap: 'SalesInvoice' doesn't have 'paid_amount' or 'status' field yet.
    
    Compromise for Phase 2: 
    Return all invoices where due_date < today.
    """
    today = timezone.now().date()
    # Filter invoices created by user, due date passed
    overdue_invoices = SalesInvoice.objects.filter(
        created_by=request.user, 
        due_date__lt=today
    ).order_by('due_date')
    
    # We should filter out those that are fully paid, but we don't track per-invoice payment yet.
    # We only assume if Created > Balance, some might be paid.
    # For a true report, we need to allocate payments to invoices (Knock-off).
    # That is complex. For now, LIST ALL invoices past due date.
    
    serializer = SalesInvoiceSerializer(overdue_invoices, many=True)
    
    # Enrich data with 'days_overdue'
    data = serializer.data
    for item in data:
        due_date = datetime.datetime.strptime(item['due_date'], "%Y-%m-%d").date()
        item['days_overdue'] = (today - due_date).days
        
    return Response(data)

from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import ClientLedgerEntry
from .serializers import ClientLedgerEntrySerializer
from .services import AccountingService
from billing.models import Customer
from datetime import date
from decimal import Decimal
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

# Create your views here.

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('customer', openapi.IN_QUERY, description="Customer ID", type=openapi.TYPE_STRING),
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="List of client ledger entries",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "customer": "uuid-cust-1",
                    "date": "2025-08-01",
                    "description": "Sale Invoice INV-001",
                    "invoice": "uuid-inv-1",
                    "debit": 1000,
                    "credit": 0,
                    "balance": 1000,
                    "created_by": 1,
                    "created_at": "2025-08-01T10:00:00Z"
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def client_ledger_list(request):
    customer = request.query_params.get('customer')
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    queryset = ClientLedgerEntry.objects.select_related('customer').filter(created_by=request.user)
    if customer:
        queryset = queryset.filter(customer=customer)
    if date_from and date_to:
        queryset = queryset.filter(date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(date__lte=date_to)
    queryset = queryset.order_by('date', 'created_at')
    serializer = ClientLedgerEntrySerializer(queryset, many=True)
    return Response(serializer.data)

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=ClientLedgerEntrySerializer,
    responses={200: openapi.Response(
        description="Updated ledger entry",
        examples={
            "application/json": {
                "id": "uuid-1",
                "customer": "uuid-cust-1",
                "date": "2025-08-01",
                "description": "Correction",
                "invoice": None,
                "debit": 0,
                "credit": 500,
                "balance": 500,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z"
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def client_ledger_edit(request, pk):
    try:
        entry = ClientLedgerEntry.objects.select_related('customer').get(pk=pk, created_by=request.user)
    except ClientLedgerEntry.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Ledger entry not found.'
        }, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        print(f"DEBUG: Request data: {request.data}")
        print(f"DEBUG: Request user: {request.user}")
        
        # Pass request context - this was missing!
        serializer = ClientLedgerEntrySerializer(
            entry, 
            data=request.data, 
            partial=(request.method == 'PATCH'),
            context={'request': request}  # This is crucial!
        )
        
        if serializer.is_valid():
            print("DEBUG: Serializer is valid, saving...")
            serializer.save()
            return Response({
                'success': True,
                'message': 'Ledger entry updated successfully.',
                'data': serializer.data
            })
        else:
            print(f"DEBUG: Serializer errors: {serializer.errors}")
            return Response({
                'success': False,
                'message': 'Validation errors occurred.',
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
            
    elif request.method == 'DELETE':
        entry.delete()
        return Response({
            'success': True,
            'message': 'Ledger entry deleted successfully.'
        }, status=status.HTTP_204_NO_CONTENT)

@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['customer', 'amount'],
        properties={
            'customer': openapi.Schema(type=openapi.TYPE_STRING, description='Customer ID'),
            'amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Payment amount'),
            'description': openapi.Schema(type=openapi.TYPE_STRING, description='Description', default='Payment received'),
            'date': openapi.Schema(type=openapi.TYPE_STRING, format='date', description='Date (YYYY-MM-DD)'),
        }
    ),
    responses={201: openapi.Response(
        description="Payment entry created",
        examples={
            "application/json": {
                "id": "uuid-2",
                "customer": "uuid-cust-1",
                "date": "2025-08-05",
                "description": "Payment received",
                "invoice": None,
                "debit": 0,
                "credit": 500,
                "balance": 500,
                "created_by": 1,
                "created_at": "2025-08-05T12:00:00Z"
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def client_payment_entry(request):
    """
    Record a payment received from a client (credit entry).
    Uses double-entry accounting to automatically create proper journal entries.
    Required fields: customer, amount, description (optional), date (optional)
    """
    customer_id = request.data.get('customer')
    amount = request.data.get('amount')
    description = request.data.get('description', 'Payment received')
    entry_date = request.data.get('date', date.today())

    if not customer_id or not amount:
        return Response({
            'success': False,
            'error': 'customer and amount are required.'
        }, status=status.HTTP_400_BAD_REQUEST)
        
    try:
        customer = Customer.objects.get(id=customer_id, created_by=request.user)
    except Customer.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Customer not found.'
        }, status=status.HTTP_404_NOT_FOUND)

    try:
        # Use AccountingService to create proper double-entry accounting entries
        client_ledger_entry, journal_entry = AccountingService.create_payment_received_entries(
            customer=customer,
            amount=Decimal(str(amount)),
            description=description,
            date=entry_date,
            user=request.user
        )
        
        # Return the client ledger entry with full customer object
        client_ledger_entry = ClientLedgerEntry.objects.select_related('customer').get(pk=client_ledger_entry.pk)
        serializer = ClientLedgerEntrySerializer(client_ledger_entry, context={'request': request})
        
        return Response({
            'success': True,
            'message': 'Payment entry created successfully.',
            'data': serializer.data,
            'journal_entry_id': str(journal_entry.id)
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': f'Failed to create payment entry: {str(e)}'
        }, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Ledger summary per client",
        examples={
            "application/json": [
                {
                    "customer_id": "uuid-cust-1",
                    "customer_name": "Customer X",
                    "balance": 500
                },
                {
                    "customer_id": "uuid-cust-2",
                    "customer_name": "Customer Y",
                    "balance": 0
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def client_ledger_summary(request):
    summary = []
    customers = Customer.objects.filter(created_by=request.user)
    for customer in customers:
        last_entry = ClientLedgerEntry.objects.filter(customer=customer, created_by=request.user).order_by('-date', '-created_at').first()
        balance = last_entry.balance if last_entry else 0
        summary.append({
            'customer_id': str(customer.id),
            'customer_name': customer.name,
            'balance': balance
        })
    return Response(summary)

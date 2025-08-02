from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import ClientLedgerEntry
from .serializers import ClientLedgerEntrySerializer
from billing.models import Customer
from datetime import date
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
    queryset = ClientLedgerEntry.objects.filter(created_by=request.user)
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
        entry = ClientLedgerEntry.objects.get(pk=pk, created_by=request.user)
    except ClientLedgerEntry.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = ClientLedgerEntrySerializer(entry, data=request.data, partial=(request.method == 'PATCH'))
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

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
    Required fields: customer, amount, description (optional), date (optional)
    """
    customer_id = request.data.get('customer')
    amount = request.data.get('amount')
    description = request.data.get('description', 'Payment received')
    entry_date = request.data.get('date', date.today())

    if not customer_id or not amount:
        return Response({'error': 'customer and amount are required.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        customer = Customer.objects.get(id=customer_id, created_by=request.user)
    except Customer.DoesNotExist:
        return Response({'error': 'Customer not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Calculate running balance
    last_entry = ClientLedgerEntry.objects.filter(customer=customer, created_by=request.user).order_by('-date', '-created_at').first()
    prev_balance = last_entry.balance if last_entry else 0
    new_balance = prev_balance - float(amount)

    entry = ClientLedgerEntry.objects.create(
        customer=customer,
        date=entry_date,
        description=description,
        debit=0,
        credit=amount,
        balance=new_balance,
        created_by=request.user
    )
    serializer = ClientLedgerEntrySerializer(entry)
    return Response(serializer.data, status=status.HTTP_201_CREATED)

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

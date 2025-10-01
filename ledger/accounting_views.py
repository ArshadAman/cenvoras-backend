from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Q
from .models import Account, JournalEntry, GeneralLedgerEntry, AccountType
from .serializers import AccountSerializer, JournalEntrySerializer, AccountBalanceSerializer
from .services import AccountingService


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('account_type', openapi.IN_QUERY, description="Filter by account type", type=openapi.TYPE_STRING),
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by name or code", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Chart of Accounts",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "code": "1001",
                    "name": "Cash",
                    "account_type": "asset",
                    "parent_account": None,
                    "description": "Cash account",
                    "is_active": True
                }
            ]
        }
    )}
)
@swagger_auto_schema(
    method='post',
    request_body=AccountSerializer,
    responses={201: openapi.Response(description="Account created successfully")}
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def chart_of_accounts(request):
    """Chart of Accounts - List and Create accounts"""
    if request.method == 'GET':
        accounts = Account.objects.filter(created_by=request.user)
        
        # Filter by account type
        account_type = request.query_params.get('account_type')
        if account_type:
            accounts = accounts.filter(account_type=account_type)
        
        # Search by name or code
        search = request.query_params.get('search')
        if search:
            accounts = accounts.filter(
                Q(name__icontains=search) | Q(code__icontains=search)
            )
        
        accounts = accounts.order_by('account_type', 'code', 'name')
        serializer = AccountSerializer(accounts, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = AccountSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                'success': True,
                'message': 'Account created successfully.',
                'data': serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('account', openapi.IN_QUERY, description="Filter by account ID", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="Journal Entries",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "date": "2025-10-01",
                    "description": "Sales Invoice - John Doe",
                    "reference": "INV-001",
                    "ledger_entries": [
                        {
                            "account_name": "Accounts Receivable",
                            "debit": 1000.00,
                            "credit": 0.00
                        },
                        {
                            "account_name": "Sales Revenue",
                            "debit": 0.00,
                            "credit": 1000.00
                        }
                    ],
                    "total_debits": 1000.00,
                    "total_credits": 1000.00,
                    "is_balanced": True
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def journal_entries(request):
    """List journal entries with filtering options"""
    entries = JournalEntry.objects.filter(created_by=request.user).select_related(
        'sales_invoice', 'purchase_bill'
    ).prefetch_related('ledger_entries__account')
    
    # Filter by date range
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    if date_from:
        entries = entries.filter(date__gte=date_from)
    if date_to:
        entries = entries.filter(date__lte=date_to)
    
    # Filter by account
    account = request.query_params.get('account')
    if account:
        entries = entries.filter(ledger_entries__account_id=account).distinct()
    
    entries = entries.order_by('-date', '-created_at')
    serializer = JournalEntrySerializer(entries, many=True)
    return Response(serializer.data)


@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="General Ledger for specific account",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "account_name": "Accounts Receivable",
                    "debit": 1000.00,
                    "credit": 0.00,
                    "description": "Sales to John Doe",
                    "created_at": "2025-10-01T10:00:00Z"
                }
            ]
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def general_ledger(request, account_id):
    """Get general ledger entries for a specific account"""
    try:
        account = Account.objects.get(id=account_id, created_by=request.user)
    except Account.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Account not found.'
        }, status=status.HTTP_404_NOT_FOUND)
    
    entries = GeneralLedgerEntry.objects.filter(
        account=account,
        journal_entry__created_by=request.user
    ).select_related('journal_entry').order_by('-journal_entry__date', '-created_at')
    
    # Filter by date range
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    if date_from:
        entries = entries.filter(journal_entry__date__gte=date_from)
    if date_to:
        entries = entries.filter(journal_entry__date__lte=date_to)
    
    serializer = GeneralLedgerEntry.objects.filter(id__in=[e.id for e in entries])
    from .serializers import GeneralLedgerEntrySerializer
    serializer = GeneralLedgerEntrySerializer(entries, many=True)
    
    # Also get account balance
    balance_info = AccountingService.get_account_balance(account, request.user)
    
    return Response({
        'account': AccountSerializer(account).data,
        'balance': balance_info,
        'entries': serializer.data
    })


@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Trial Balance",
        examples={
            "application/json": {
                "accounts": [
                    {
                        "account": {
                            "id": "uuid-1",
                            "name": "Cash",
                            "account_type": "asset"
                        },
                        "debit_total": 5000.00,
                        "credit_total": 2000.00,
                        "balance": 3000.00
                    }
                ],
                "total_debits": 10000.00,
                "total_credits": 10000.00,
                "is_balanced": True
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trial_balance(request):
    """Get trial balance for all accounts"""
    trial_balance_data = AccountingService.get_trial_balance(request.user)
    
    # Serialize the accounts data
    accounts_data = []
    for account_info in trial_balance_data['accounts']:
        accounts_data.append({
            'account': AccountSerializer(account_info['account']).data,
            'debit_total': account_info['debit_total'],
            'credit_total': account_info['credit_total'], 
            'balance': account_info['balance']
        })
    
    return Response({
        'accounts': accounts_data,
        'total_debits': trial_balance_data['total_debits'],
        'total_credits': trial_balance_data['total_credits'],
        'is_balanced': trial_balance_data['is_balanced']
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def setup_default_accounts(request):
    """Setup default chart of accounts for the user"""
    try:
        accounts = AccountingService.get_or_create_default_accounts(request.user)
        return Response({
            'success': True,
            'message': f'Successfully created {len(accounts)} default accounts.',
            'accounts': list(accounts.keys())
        })
    except Exception as e:
        return Response({
            'success': False,
            'error': f'Failed to create default accounts: {str(e)}'
        }, status=status.HTTP_400_BAD_REQUEST)
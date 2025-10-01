from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Q
from .models import Account, GeneralLedgerEntry, AccountType
from .serializers import AccountSerializer, AccountBalanceSerializer
from .services import AccountingService
import logging

logger = logging.getLogger(__name__)


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
    responses={200: openapi.Response(description="Account details")}
)
@swagger_auto_schema(
    method='put',
    request_body=AccountSerializer,
    responses={200: openapi.Response(description="Account updated successfully")}
)
@swagger_auto_schema(
    method='delete',
    responses={204: openapi.Response(description="Account deleted successfully")}
)
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def account_detail(request, account_id):
    """Get, update, or delete a specific account"""
    logger.info(f"Account {request.method} request - Account ID: {account_id}, User: {request.user.username if request.user else 'Anonymous'}")
    
    try:
        account = Account.objects.get(id=account_id, created_by=request.user)
        logger.info(f"Account found: {account.name} (Code: {account.code}, Type: {account.account_type})")
    except Account.DoesNotExist:
        logger.warning(f"Account not found for ID: {account_id}, User: {request.user.username if request.user else 'Anonymous'}")
        return Response({
            'success': False,
            'error': 'Account not found.'
        }, status=status.HTTP_404_NOT_FOUND)
    
    if request.method == 'GET':
        serializer = AccountSerializer(account)
        return Response({
            'success': True,
            'data': serializer.data
        })
    
    elif request.method == 'PUT':
        logger.info(f"PUT request for account: {account.name} (ID: {account_id}). Update data: {request.data}")
        
        serializer = AccountSerializer(account, data=request.data, context={'request': request})
        if serializer.is_valid():
            # Check if account has ledger entries before allowing certain changes
            has_entries = GeneralLedgerEntry.objects.filter(account=account).exists()
            if has_entries and 'account_type' in request.data:
                logger.warning(f"Attempted to change account type for account {account.name} which has existing ledger entries")
                return Response({
                    'success': False,
                    'error': 'Cannot change account type for accounts with existing ledger entries.'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            serializer.save()
            logger.info(f"Account {account.name} (ID: {account_id}) updated successfully")
            return Response({
                'success': True,
                'message': 'Account updated successfully.',
                'data': serializer.data
            })
        
        logger.warning(f"Invalid data for account update {account.name}: {serializer.errors}")
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'DELETE':
        logger.info(f"DELETE request for account: {account.name} (ID: {account_id})")
        
        # Check if account has ledger entries
        ledger_entries = GeneralLedgerEntry.objects.filter(account=account)
        entry_count = ledger_entries.count()
        has_entries = entry_count > 0
        
        logger.info(f"Account {account.name} has {entry_count} ledger entries")
        
        if has_entries:
            # Log details about the entries preventing deletion
            sample_entries = ledger_entries[:3]  # Get first 3 entries for logging
            entry_details = []
            try:
                for entry in sample_entries:
                    entry_details.append(f"Entry ID: {entry.id}, Date: {entry.date}, Dr: ${entry.debit}, Cr: ${entry.credit}, Description: {entry.description}")
            except Exception as e:
                print(e)
                logger.warning(f"Cannot delete account {account.name} - has {entry_count} ledger entries. Sample entries: {entry_details}")
                return Response({
                    'success': False,
                    'error': f'Cannot delete account with existing ledger entries ({entry_count} entries found). Archive it instead by setting is_active to false.',
                    'entry_count': entry_count
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # Safe to delete
        logger.info(f"Deleting account {account.name} (ID: {account_id}) - no ledger entries found")
        
        try:
            account.delete()
            logger.info(f"Account {account.name} (ID: {account_id}) deleted successfully")
            return Response({
                'success': True,
                'message': 'Account deleted successfully.'
            }, status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.error(f"Error deleting account {account.name} (ID: {account_id}): {str(e)}")
            return Response({
                'success': False,
                'error': f'Error deleting account: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
    
    # Filter by date range
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    
    entries = AccountingService.get_general_ledger_entries(account, request.user, date_from, date_to)
    
    from .serializers import GeneralLedgerEntrySerializer
    serializer = GeneralLedgerEntrySerializer(entries, many=True)
    
    # Also get account balance
    balance_info = AccountingService.get_account_balance(account, request.user, date_to)
    
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


@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(description="General ledger entry details")}
)
@swagger_auto_schema(
    method='put',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'description': openapi.Schema(type=openapi.TYPE_STRING, description='Entry description'),
            'reference': openapi.Schema(type=openapi.TYPE_STRING, description='Entry reference'),
        }
    ),
    responses={200: openapi.Response(description="Entry updated successfully")}
)
@swagger_auto_schema(
    method='delete',
    responses={204: openapi.Response(description="Entry deleted successfully")}
)
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def general_ledger_entry_detail(request, entry_id):
    """Get, update, or delete a specific general ledger entry"""
    try:
        entry = GeneralLedgerEntry.objects.get(id=entry_id, created_by=request.user)
    except GeneralLedgerEntry.DoesNotExist:
        return Response({
            'success': False,
            'error': 'General ledger entry not found.'
        }, status=status.HTTP_404_NOT_FOUND)
    
    if request.method == 'GET':
        from .serializers import GeneralLedgerEntrySerializer
        serializer = GeneralLedgerEntrySerializer(entry)
        return Response({
            'success': True,
            'data': serializer.data
        })
    
    elif request.method == 'PUT':
        # Only allow editing description and reference, not amounts or accounts
        # This is to maintain accounting integrity
        updatable_fields = ['description', 'reference']
        update_data = {k: v for k, v in request.data.items() if k in updatable_fields}
        
        if not update_data:
            return Response({
                'success': False,
                'error': 'Only description and reference fields can be updated.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        for field, value in update_data.items():
            setattr(entry, field, value)
        entry.save()
        
        from .serializers import GeneralLedgerEntrySerializer
        serializer = GeneralLedgerEntrySerializer(entry)
        return Response({
            'success': True,
            'message': 'General ledger entry updated successfully.',
            'data': serializer.data
        })
    
    elif request.method == 'DELETE':
        # Check if this entry is linked to an invoice or bill
        if entry.sales_invoice or entry.purchase_bill:
            return Response({
                'success': False,
                'error': 'Cannot delete entries linked to invoices or bills. Delete the source document instead.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Store entry details for response
        entry_info = f"{entry.account.name} - Dr: ${entry.debit} Cr: ${entry.credit}"
        
        entry.delete()
        return Response({
            'success': True,
            'message': f'General ledger entry deleted successfully: {entry_info}'
        }, status=status.HTTP_204_NO_CONTENT)


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('date_from', openapi.IN_QUERY, description="Start date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('date_to', openapi.IN_QUERY, description="End date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('account', openapi.IN_QUERY, description="Filter by account ID", type=openapi.TYPE_STRING),
        openapi.Parameter('description', openapi.IN_QUERY, description="Search by description", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(description="List of all general ledger entries")}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def general_ledger_entries_list(request):
    """List all general ledger entries with filtering options"""
    entries = GeneralLedgerEntry.objects.filter(created_by=request.user).select_related('account')
    
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
        entries = entries.filter(account_id=account)
    
    # Search by description
    description = request.query_params.get('description')
    if description:
        entries = entries.filter(description__icontains=description)
    
    entries = entries.order_by('-date', '-created_at')
    
    from .serializers import GeneralLedgerEntrySerializer
    serializer = GeneralLedgerEntrySerializer(entries, many=True)
    
    return Response({
        'success': True,
        'count': len(entries),
        'entries': serializer.data
    })


@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'sales_invoice_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of the sales invoice'),
            'accounts_receivable_account_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of accounts receivable account (optional)'),
            'sales_revenue_account_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of sales revenue account (optional)'),
        },
        required=['sales_invoice_id']
    ),
    responses={
        200: openapi.Response(description="Ledger entries created successfully"),
        400: openapi.Response(description="Invalid request"),
        404: openapi.Response(description="Sales invoice not found")
    }
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_sales_invoice_ledger_entries(request):
    """Manually create ledger entries for a sales invoice"""
    try:
        from billing.models import SalesInvoice
        
        sales_invoice_id = request.data.get('sales_invoice_id')
        if not sales_invoice_id:
            return Response({
                'success': False,
                'error': 'sales_invoice_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the sales invoice
        try:
            sales_invoice = SalesInvoice.objects.get(
                id=sales_invoice_id,
                created_by=request.user
            )
        except SalesInvoice.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Sales invoice not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if ledger entries already exist
        existing_entries = GeneralLedgerEntry.objects.filter(
            sales_invoice=sales_invoice,
            created_by=request.user
        )
        if existing_entries.exists():
            return Response({
                'success': False,
                'error': f'Ledger entries already exist for this sales invoice ({existing_entries.count()} entries found)',
                'existing_entries_count': existing_entries.count()
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get optional specific accounts
        accounts_receivable_account = None
        sales_revenue_account = None
        
        ar_account_id = request.data.get('accounts_receivable_account_id')
        if ar_account_id:
            try:
                accounts_receivable_account = Account.objects.get(
                    id=ar_account_id,
                    created_by=request.user,
                    account_type=AccountType.ASSET
                )
            except Account.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Accounts receivable account not found or not an asset account'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        sr_account_id = request.data.get('sales_revenue_account_id')
        if sr_account_id:
            try:
                sales_revenue_account = Account.objects.get(
                    id=sr_account_id,
                    created_by=request.user,
                    account_type=AccountType.REVENUE
                )
            except Account.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Sales revenue account not found or not a revenue account'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create the ledger entries
        success = AccountingService.create_sales_invoice_entries(
            sales_invoice,
            accounts_receivable_account=accounts_receivable_account,
            sales_revenue_account=sales_revenue_account
        )
        
        if success:
            # Count the created entries
            created_entries = GeneralLedgerEntry.objects.filter(
                sales_invoice=sales_invoice,
                created_by=request.user
            )
            
            return Response({
                'success': True,
                'message': f'Successfully created ledger entries for Sales Invoice {sales_invoice.invoice_number}',
                'entries_created': created_entries.count(),
                'sales_invoice_number': sales_invoice.invoice_number,
                'total_amount': sales_invoice.total_amount
            })
        else:
            return Response({
                'success': False,
                'error': 'Failed to create ledger entries'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f"Error creating sales invoice ledger entries: {str(e)}")
        return Response({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'purchase_bill_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of the purchase bill'),
            'purchases_account_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of purchases/expense account (optional)'),
            'accounts_payable_account_id': openapi.Schema(type=openapi.TYPE_STRING, description='UUID of accounts payable account (optional)'),
        },
        required=['purchase_bill_id']
    ),
    responses={
        200: openapi.Response(description="Ledger entries created successfully"),
        400: openapi.Response(description="Invalid request"),
        404: openapi.Response(description="Purchase bill not found")
    }
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_purchase_bill_ledger_entries(request):
    """Manually create ledger entries for a purchase bill"""
    try:
        from billing.models import PurchaseBill
        
        purchase_bill_id = request.data.get('purchase_bill_id')
        if not purchase_bill_id:
            return Response({
                'success': False,
                'error': 'purchase_bill_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the purchase bill
        try:
            purchase_bill = PurchaseBill.objects.get(
                id=purchase_bill_id,
                created_by=request.user
            )
        except PurchaseBill.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Purchase bill not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if ledger entries already exist
        existing_entries = GeneralLedgerEntry.objects.filter(
            purchase_bill=purchase_bill,
            created_by=request.user
        )
        if existing_entries.exists():
            return Response({
                'success': False,
                'error': f'Ledger entries already exist for this purchase bill ({existing_entries.count()} entries found)',
                'existing_entries_count': existing_entries.count()
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get optional specific accounts
        purchases_account = None
        accounts_payable_account = None
        
        p_account_id = request.data.get('purchases_account_id')
        if p_account_id:
            try:
                purchases_account = Account.objects.get(
                    id=p_account_id,
                    created_by=request.user,
                    account_type=AccountType.EXPENSE
                )
            except Account.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Purchases account not found or not an expense account'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        ap_account_id = request.data.get('accounts_payable_account_id')
        if ap_account_id:
            try:
                accounts_payable_account = Account.objects.get(
                    id=ap_account_id,
                    created_by=request.user,
                    account_type=AccountType.LIABILITY
                )
            except Account.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Accounts payable account not found or not a liability account'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create the ledger entries
        success = AccountingService.create_purchase_bill_entries(
            purchase_bill,
            purchases_account=purchases_account,
            accounts_payable_account=accounts_payable_account
        )
        
        if success:
            # Count the created entries
            created_entries = GeneralLedgerEntry.objects.filter(
                purchase_bill=purchase_bill,
                created_by=request.user
            )
            
            return Response({
                'success': True,
                'message': f'Successfully created ledger entries for Purchase Bill {purchase_bill.bill_number}',
                'entries_created': created_entries.count(),
                'bill_number': purchase_bill.bill_number,
                'total_amount': purchase_bill.total_amount
            })
        else:
            return Response({
                'success': False,
                'error': 'Failed to create ledger entries'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f"Error creating purchase bill ledger entries: {str(e)}")
        return Response({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
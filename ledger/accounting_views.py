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
from django.db.models import Sum, Avg, Max, Count, F
from django.utils import timezone
from datetime import timedelta
import decimal

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
    tenant = getattr(request.user, 'active_tenant', request.user)
    if request.method == 'GET':
        accounts = Account.objects.filter(created_by=tenant)
        
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        account = Account.objects.get(id=account_id, created_by=tenant)
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
                'error': 'Unable to delete account right now. Please try again later.'
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        account = Account.objects.get(id=account_id, created_by=tenant)
    except Account.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Account not found.'
        }, status=status.HTTP_404_NOT_FOUND)
    
    # Filter by date range
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    
    entries = AccountingService.get_general_ledger_entries(account, tenant, date_from, date_to)
    
    from .serializers import GeneralLedgerEntrySerializer
    serializer = GeneralLedgerEntrySerializer(entries, many=True)
    
    # Also get account balance
    balance_info = AccountingService.get_account_balance(account, tenant, date_to)
    
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
            'error': 'Failed to create default accounts. Please try again later.'
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        entry = GeneralLedgerEntry.objects.get(id=entry_id, created_by=tenant)
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
        # Accounting Integrity: Individual ledger legs cannot be deleted independently.
        # Deleting a single leg corrupts the double-entry accounting equation (Debits != Credits).
        if entry.sales_invoice:
            return Response({
                'success': False,
                'error': 'Cannot delete entries linked to a Sales Invoice. Void or delete the invoice to remove entries atomically.'
            }, status=status.HTTP_400_BAD_REQUEST)

        if entry.purchase_bill:
            return Response({
                'success': False,
                'error': 'Cannot delete entries linked to a Purchase Bill. Void or delete the bill to remove entries atomically.'
            }, status=status.HTTP_400_BAD_REQUEST)

        if entry.credit_note or entry.debit_note or entry.payment:
            return Response({
                'success': False,
                'error': 'Cannot delete entries linked to a formal voucher. Cancel or reverse the source document.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # For manual entries, prohibit single-leg deletion to maintain balanced books
        return Response({
            'success': False,
            'error': 'General ledger entries cannot be deleted individually. Create a reversing journal entry to adjust balances.'
        }, status=status.HTTP_400_BAD_REQUEST)


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
    """
    List general ledger entries with opening balance, running balance,
    partner/account filtering, and server-side pagination.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    base_entries = GeneralLedgerEntry.objects.filter(created_by=tenant)
    
    # 1. Filter parameters
    account_id = request.query_params.get('account')
    customer_id = request.query_params.get('customer')
    vendor_id = request.query_params.get('vendor')
    description = request.query_params.get('description')
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    ordering = request.query_params.get('ordering', '-date')

    target_account = None
    target_customer = None
    target_vendor = None

    if account_id:
        base_entries = base_entries.filter(account_id=account_id)
        target_account = Account.objects.filter(id=account_id, created_by=tenant).first()
    if customer_id:
        base_entries = base_entries.filter(customer_id=customer_id)
        from billing.models import Customer
        target_customer = Customer.objects.filter(id=customer_id, created_by=tenant).first()
    if vendor_id:
        base_entries = base_entries.filter(vendor_id=vendor_id)
        from billing.models import Vendor
        target_vendor = Vendor.objects.filter(id=vendor_id, created_by=tenant).first()

    if description:
        base_entries = base_entries.filter(description__icontains=description)

    # 2. Compute Opening Balance (prior to date_from)
    from decimal import Decimal
    opening_balance = Decimal('0.00')
    opening_balance_type = 'Dr'

    if date_from:
        prior_entries = GeneralLedgerEntry.objects.filter(created_by=tenant, date__lt=date_from)
        if account_id:
            prior_entries = prior_entries.filter(account_id=account_id)
        if customer_id:
            prior_entries = prior_entries.filter(customer_id=customer_id)
        if vendor_id:
            prior_entries = prior_entries.filter(vendor_id=vendor_id)

        prior_agg = prior_entries.aggregate(d_sum=Sum('debit'), c_sum=Sum('credit'))
        p_debits = prior_agg['d_sum'] or Decimal('0.00')
        p_credits = prior_agg['c_sum'] or Decimal('0.00')

        if target_vendor:
            # Vendor is Accounts Payable (Credit normal)
            net_op = p_credits - p_debits
            opening_balance = abs(net_op)
            opening_balance_type = 'Cr' if net_op >= 0 else 'Dr'
        elif target_account and target_account.account_type in [AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE]:
            net_op = p_credits - p_debits
            opening_balance = abs(net_op)
            opening_balance_type = 'Cr' if net_op >= 0 else 'Dr'
        else:
            # Customer (Accounts Receivable) or Asset/Expense: Debit normal
            net_op = p_debits - p_credits
            opening_balance = abs(net_op)
            opening_balance_type = 'Dr' if net_op >= 0 else 'Cr'

    # 3. Filter period entries
    period_entries = base_entries
    if date_from:
        period_entries = period_entries.filter(date__gte=date_from)
    if date_to:
        period_entries = period_entries.filter(date__lte=date_to)

    # Aggregate period totals
    period_agg = period_entries.aggregate(d_sum=Sum('debit'), c_sum=Sum('credit'))
    period_debit_total = period_agg['d_sum'] or Decimal('0.00')
    period_credit_total = period_agg['c_sum'] or Decimal('0.00')

    # Compute closing balance
    if target_vendor or (target_account and target_account.account_type in [AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE]):
        signed_op = opening_balance if opening_balance_type == 'Cr' else -opening_balance
        net_closing = signed_op + period_credit_total - period_debit_total
        closing_balance = abs(net_closing)
        closing_balance_type = 'Cr' if net_closing >= 0 else 'Dr'
    else:
        signed_op = opening_balance if opening_balance_type == 'Dr' else -opening_balance
        net_closing = signed_op + period_debit_total - period_credit_total
        closing_balance = abs(net_closing)
        closing_balance_type = 'Dr' if net_closing >= 0 else 'Cr'

    # 4. Compute Running Balances (Chronological pass)
    chronological_entries = list(period_entries.select_related(
        'account', 'customer', 'vendor', 'sales_invoice', 'purchase_bill', 'credit_note', 'debit_note'
    ).order_by('date', 'created_at', 'id'))

    is_credit_normal = target_vendor or (target_account and target_account.account_type in [AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE])
    current_running = (opening_balance if opening_balance_type == 'Cr' else -opening_balance) if is_credit_normal else (opening_balance if opening_balance_type == 'Dr' else -opening_balance)

    for entry in chronological_entries:
        if is_credit_normal:
            current_running += (entry.credit - entry.debit)
            entry.running_balance = abs(current_running)
            entry.running_balance_type = 'Cr' if current_running >= 0 else 'Dr'
        else:
            current_running += (entry.debit - entry.credit)
            entry.running_balance = abs(current_running)
            entry.running_balance_type = 'Dr' if current_running >= 0 else 'Cr'

    # Order by user preference (default desc for ledger tables)
    if ordering.startswith('-'):
        entries_list = list(reversed(chronological_entries))
    else:
        entries_list = chronological_entries

    total_count = len(entries_list)

    # 5. Server-side Pagination
    page_param = request.query_params.get('page')
    page_size_param = request.query_params.get('page_size')
    
    if page_param or page_size_param:
        try:
            page = max(1, int(page_param or 1))
            page_size = max(1, min(200, int(page_size_param or 20)))
        except ValueError:
            page = 1
            page_size = 20

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_entries = entries_list[start_idx:end_idx]
        total_pages = (total_count + page_size - 1) // page_size if page_size else 1
    else:
        page = 1
        page_size = 20
        start_idx = 0
        end_idx = page_size
        paginated_entries = entries_list[start_idx:end_idx]
        total_pages = (total_count + page_size - 1) // page_size if page_size else 1

    from .serializers import GeneralLedgerEntrySerializer
    serializer = GeneralLedgerEntrySerializer(paginated_entries, many=True)

    partner_payload = None
    if target_customer:
        partner_payload = {
            'type': 'customer',
            'id': str(target_customer.id),
            'name': target_customer.name,
            'email': target_customer.email or '',
            'phone': target_customer.phone or '',
            'gstin': getattr(target_customer, 'gstin', '') or '',
            'state': getattr(target_customer, 'state', '') or '',
        }
    elif target_vendor:
        partner_payload = {
            'type': 'vendor',
            'id': str(target_vendor.id),
            'name': target_vendor.name,
            'email': target_vendor.email or '',
            'phone': target_vendor.phone or '',
            'gstin': getattr(target_vendor, 'gstin', '') or '',
            'state': getattr(target_vendor, 'state', '') or '',
        }

    return Response({
        'success': True,
        'count': total_count,
        'total_pages': total_pages,
        'current_page': page,
        'page_size': page_size,
        'opening_balance': opening_balance,
        'opening_balance_type': opening_balance_type,
        'period_debit_total': period_debit_total,
        'period_credit_total': period_credit_total,
        'closing_balance': closing_balance,
        'closing_balance_type': closing_balance_type,
        'partner': partner_payload,
        'entries': serializer.data
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def partner_statement(request):
    """
    Dedicated endpoint for Customer or Vendor Statement of Account.
    Query params:
      partner_type: 'customer' | 'vendor'
      partner_id: UUID
      date_from, date_to, page, page_size
    """
    partner_type = request.query_params.get('partner_type', 'customer').lower()
    partner_id = request.query_params.get('partner_id')
    if not partner_id:
        return Response({'success': False, 'error': 'partner_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Forward to general_ledger_entries_list by injecting query parameters
    req_get = request.GET.copy()
    if partner_type == 'vendor':
        req_get['vendor'] = partner_id
    else:
        req_get['customer'] = partner_id
    request._request.GET = req_get

    return general_ledger_entries_list(request._request)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def partner_statement_pdf(request):
    """
    Generate and stream pixel-perfect Vector PDF Statement of Account via Warm Chromium CDP.
    Query params:
      partner_type: 'customer' | 'vendor'
      partner_id: UUID
      date_from, date_to
    """
    from django.http import HttpResponse
    from billing.html_pdf_service import render_html_to_vector_pdf
    from decimal import Decimal

    tenant = getattr(request.user, 'active_tenant', request.user)
    partner_type = request.query_params.get('partner_type', 'customer').lower()
    partner_id = request.query_params.get('partner_id')

    if not partner_id:
        return HttpResponse("partner_id is required", status=400)

    # Fetch partner
    partner_name = "Account"
    partner_details = {}
    if partner_type == 'vendor':
        from billing.models import Vendor
        vendor = Vendor.objects.filter(id=partner_id, created_by=tenant).first()
        if not vendor:
            return HttpResponse("Vendor not found", status=404)
        partner_name = vendor.name
        partner_details = {
            'name': vendor.name,
            'email': vendor.email or '',
            'phone': vendor.phone or '',
            'gstin': vendor.gstin or '',
            'address': vendor.address or '',
            'state': getattr(vendor, 'state', '') or '',
            'type_label': 'Vendor / Creditor'
        }
    else:
        from billing.models import Customer
        customer = Customer.objects.filter(id=partner_id, created_by=tenant).first()
        if not customer:
            return HttpResponse("Customer not found", status=404)
        partner_name = customer.name
        partner_details = {
            'name': customer.name,
            'email': customer.email or '',
            'phone': customer.phone or '',
            'gstin': customer.gstin or '',
            'address': customer.address or '',
            'state': getattr(customer, 'state', '') or '',
            'type_label': 'Client / Debtor'
        }

    # Query all entries in range (no pagination for full PDF statement)
    req_get = request.GET.copy()
    req_get['page_size'] = '1000'
    req_get['page'] = '1'
    if partner_type == 'vendor':
        req_get['vendor'] = partner_id
    else:
        req_get['customer'] = partner_id
    request._request.GET = req_get

    resp = general_ledger_entries_list(request._request)
    data = resp.data

    date_from_str = request.query_params.get('date_from') or 'Beginning'
    date_to_str = request.query_params.get('date_to') or timezone.now().strftime('%Y-%m-%d')
    company_name = getattr(tenant, 'company_name', None) or getattr(tenant, 'name', None) or 'Business'
    company_gstin = getattr(tenant, 'gstin', '') or ''
    company_phone = getattr(tenant, 'phone', '') or ''
    company_email = getattr(tenant, 'email', '') or ''

    # Build HTML rows
    table_rows = []
    for entry in data.get('entries', []):
        d_val = f"₹{Decimal(str(entry['debit'])):,.2f}" if Decimal(str(entry['debit'])) > 0 else "-"
        c_val = f"₹{Decimal(str(entry['credit'])):,.2f}" if Decimal(str(entry['credit'])) > 0 else "-"
        r_val = f"₹{Decimal(str(entry.get('running_balance', 0))):,.2f} {entry.get('running_balance_type', '')}"
        table_rows.append(f"""
            <tr style="border-bottom: 1px solid #e5e7eb; font-size: 11px;">
                <td style="padding: 8px 10px; white-space: nowrap;">{entry['date']}</td>
                <td style="padding: 8px 10px; font-weight: 600;">{entry.get('reference') or '-'}</td>
                <td style="padding: 8px 10px; max-width: 250px;">{entry['description']}</td>
                <td style="padding: 8px 10px; text-align: right; color: #b91c1c; font-family: monospace;">{d_val}</td>
                <td style="padding: 8px 10px; text-align: right; color: #15803d; font-family: monospace;">{c_val}</td>
                <td style="padding: 8px 10px; text-align: right; font-weight: 700; font-family: monospace;">{r_val}</td>
            </tr>
        """)

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Statement of Account - {partner_name}</title>
<style>
    @page {{ size: A4 portrait; margin: 15mm 15mm 15mm 15mm; }}
    body {{ font-family: 'DejaVu Sans', 'Noto Sans', sans-serif; color: #111827; margin: 0; padding: 0; background: #fff; }}
    .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #111827; padding-bottom: 14px; margin-bottom: 20px; }}
    .title {{ font-size: 22px; font-weight: 900; letter-spacing: -0.5px; text-transform: uppercase; color: #111827; }}
    .subtitle {{ font-size: 11px; color: #6b7280; margin-top: 4px; }}
    .company-info {{ text-align: right; font-size: 11px; line-height: 1.5; color: #374151; }}
    .kpi-grid {{ display: flex; gap: 12px; margin-bottom: 22px; }}
    .kpi-card {{ flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 14px; background: #f9fafb; }}
    .kpi-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; color: #6b7280; }}
    .kpi-val {{ font-size: 16px; font-weight: 800; margin-top: 4px; font-family: monospace; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th {{ background: #f3f4f6; text-align: left; padding: 8px 10px; font-size: 10px; font-weight: 800; text-transform: uppercase; color: #374151; border-bottom: 2px solid #d1d5db; }}
</style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">Statement of Account</div>
            <div class="subtitle">Period: <strong>{date_from_str}</strong> to <strong>{date_to_str}</strong></div>
            <div style="margin-top: 10px; font-size: 12px; line-height: 1.5;">
                <span style="font-size: 9px; font-weight: 800; color: #6b7280; text-transform: uppercase;">Statement For</span><br/>
                <strong style="font-size: 14px; color: #111827;">{partner_details.get('name')}</strong><br/>
                {f"GSTIN: {partner_details['gstin']}<br/>" if partner_details.get('gstin') else ''}
                {f"Phone: {partner_details['phone']}<br/>" if partner_details.get('phone') else ''}
                {f"Address: {partner_details['address']}<br/>" if partner_details.get('address') else ''}
            </div>
        </div>
        <div class="company-info">
            <strong style="font-size: 16px; color: #111827;">{company_name}</strong><br/>
            {f"GSTIN: {company_gstin}<br/>" if company_gstin else ''}
            {f"Phone: {company_phone}<br/>" if company_phone else ''}
            {f"Email: {company_email}<br/>" if company_email else ''}
        </div>
    </div>

    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">Opening Balance</div>
            <div class="kpi-val">₹{Decimal(str(data.get('opening_balance', 0))):,.2f} {data.get('opening_balance_type', '')}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Debits</div>
            <div class="kpi-val" style="color: #b91c1c;">₹{Decimal(str(data.get('period_debit_total', 0))):,.2f}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Credits</div>
            <div class="kpi-val" style="color: #15803d;">₹{Decimal(str(data.get('period_credit_total', 0))):,.2f}</div>
        </div>
        <div class="kpi-card" style="background: #111827; border-color: #111827; color: #fff;">
            <div class="kpi-label" style="color: #9ca3af;">Closing Balance</div>
            <div class="kpi-val" style="color: #fff;">₹{Decimal(str(data.get('closing_balance', 0))):,.2f} {data.get('closing_balance_type', '')}</div>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width: 80px;">Date</th>
                <th style="width: 110px;">Reference</th>
                <th>Description</th>
                <th style="width: 100px; text-align: right;">Debit (Dr)</th>
                <th style="width: 100px; text-align: right;">Credit (Cr)</th>
                <th style="width: 120px; text-align: right;">Balance</th>
            </tr>
        </thead>
        <tbody>
            {"".join(table_rows) if table_rows else '<tr><td colspan="6" style="text-align: center; padding: 24px; color: #6b7280;">No transactions during this period</td></tr>'}
        </tbody>
    </table>

    <div style="margin-top: 36px; padding-top: 14px; border-top: 1px solid #e5e7eb; display: flex; justify-content: space-between; font-size: 10px; color: #6b7280;">
        <div>Generated by Cenvoras Accounting Engine &bull; {timezone.now().strftime('%d %b %Y, %I:%M %p')}</div>
        <div style="text-align: right;">Authorized Signatory</div>
    </div>
</body>
</html>
"""

    try:
        pdf_bytes = render_html_to_vector_pdf(html_content, landscape=False, print_background=True)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        safe_name = partner_name.replace(' ', '_')
        response['Content-Disposition'] = f'attachment; filename="Statement_{safe_name}.pdf"'
        return response
    except Exception as e:
        logger.error(f"Error rendering statement vector PDF: {e}")
        return HttpResponse(f"Error generating PDF: {str(e)}", status=500)



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
        
        tenant = getattr(request.user, 'active_tenant', request.user)
        # Get the sales invoice
        try:
            sales_invoice = SalesInvoice.objects.get(
                id=sales_invoice_id,
                created_by=tenant
            )
        except SalesInvoice.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Sales invoice not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if ledger entries already exist
        existing_entries = GeneralLedgerEntry.objects.filter(
            sales_invoice=sales_invoice,
            created_by=tenant
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
                    created_by=tenant,
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
                    created_by=tenant,
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
                created_by=tenant
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
            'error': 'Internal server error while creating sales invoice ledger entries.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ledger_stats(request):
    """Get ledger statistics for the dashboard"""
    user = getattr(request.user, 'active_tenant', request.user)
    
    # Date range filters
    date_from_str = request.query_params.get('date_from')
    date_to_str = request.query_params.get('date_to')
    customer_id = request.query_params.get('customer')
    
    # Base queries
    from .models import GeneralLedgerEntry
    entries = GeneralLedgerEntry.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user)
    )
    if date_from_str:
        entries = entries.filter(date__gte=date_from_str)
    if date_to_str:
        entries = entries.filter(date__lte=date_to_str)
    if customer_id:
        entries = entries.filter(customer_id=customer_id)
        
    # Stats calculation
    # Stats calculation - accurately distinguishing between sales, returns, and payments
    from billing.models_returns import CreditNote, DebitNote
    
    # Total Invoices (Sales volume)
    total_invoices = entries.filter(
        account__account_type='asset', 
        debit__gt=0, 
        sales_invoice__isnull=False
    ).aggregate(total=Sum('debit'))['total'] or 0
    
    # Total Payments (Money received from customers)
    total_payments = entries.filter(
        account__account_type='asset', 
        credit__gt=0, 
        sales_invoice__isnull=True # Payments usually don't link to a single invoice if they are bulk
    ).aggregate(total=Sum('credit'))['total'] or 0
    
    # Note: If payments ARE linked to invoices, we might need a better check.
    # But usually, Returns have a 'credit_note' link.
    
    total_returns = entries.filter(
        account__account_type='asset',
        credit__gt=0,
        credit_note__isnull=False
    ).aggregate(total=Sum('credit'))['total'] or 0
    
    # Adjust total_invoices for net sales if desired, or keep separate. 
    # Usually, dashboard wants "Net Sales".
    net_sales = float(total_invoices) - float(total_returns)
    
    net_balance = entries.aggregate(balance=Sum('debit') - Sum('credit'))['balance'] or 0
    
    # Customer specific logic
    from billing.models import Customer, SalesInvoice, BillPaymentStatus
    customers_query = Customer.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user)
    )
    if customer_id:
        customers_query = customers_query.filter(id=customer_id)
        
    total_customers = customers_query.count()
    outstanding_balance = customers_query.aggregate(total=Sum('current_balance'))['total'] or 0

    # Overdue invoice stats
    overdue_query = SalesInvoice.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user),
        due_date__lt=timezone.now().date(),
        payment_status__in=[BillPaymentStatus.PENDING, BillPaymentStatus.PARTIAL_PAID]
    )
    if customer_id:
        overdue_query = overdue_query.filter(customer_id=customer_id)

    overdue_stats = overdue_query.aggregate(
        overdue_count=Count('id'),
        overdue_total=Sum(F('total_amount') - F('amount_paid'))
    )

    # Reconciliation stats: compare customer balance with invoice-level outstanding
    invoice_outstanding = SalesInvoice.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user)
    )
    if customer_id:
        invoice_outstanding = invoice_outstanding.filter(customer_id=customer_id)

    invoice_outstanding_total = invoice_outstanding.aggregate(
        total=Sum(F('total_amount') - F('amount_paid'))
    )['total'] or 0

    unapplied_credits = max(float(invoice_outstanding_total) - float(outstanding_balance), 0.0)
    unmapped_outstanding = max(float(outstanding_balance) - float(invoice_outstanding_total), 0.0)
    
    # Recent transactions (last 30 days)
    thirty_days_ago = timezone.now().date() - timedelta(days=30)
    recent_count = GeneralLedgerEntry.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user),
        date__gte=thirty_days_ago
    ).count()
    
    # Average and Largest
    payment_stats = GeneralLedgerEntry.objects.filter(
        Q(created_by=user) | Q(created_by__parent=user),
        account__account_type='asset', 
        credit__gt=0
    ).aggregate(
        avg=Avg('credit'),
        max=Max('credit')
    )
    
    return Response({
        'total_payments': float(total_payments),
        'total_invoices': float(net_sales), # Return Net Sales for the dashboard
        'net_balance': float(net_balance),
        'total_customers': total_customers,
        'recent_transactions': recent_count,
        'average_payment': float(payment_stats['avg'] or 0),
        'largest_payment': float(payment_stats['max'] or 0),
        'outstanding_balance': float(outstanding_balance),
        'overdue_invoices_count': overdue_stats['overdue_count'] or 0,
        'overdue_amount': float(overdue_stats['overdue_total'] or 0),
        'invoice_outstanding_total': float(invoice_outstanding_total or 0),
        'unapplied_credits': unapplied_credits,
        'unmapped_outstanding': unmapped_outstanding,
        'reconciliation_gap': float(outstanding_balance) - float(invoice_outstanding_total or 0),
    })


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
        
        tenant = getattr(request.user, 'active_tenant', request.user)
        # Get the purchase bill
        try:
            purchase_bill = PurchaseBill.objects.get(
                id=purchase_bill_id,
                created_by=tenant
            )
        except PurchaseBill.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Purchase bill not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if ledger entries already exist
        existing_entries = GeneralLedgerEntry.objects.filter(
            purchase_bill=purchase_bill,
            created_by=tenant
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
                    created_by=tenant,
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
                    created_by=tenant,
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
                created_by=tenant
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
            'error': 'Internal server error while creating purchase bill ledger entries.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            'date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Date of journal entry'),
            'description': openapi.Schema(type=openapi.TYPE_STRING, description='Global description'),
            'reference': openapi.Schema(type=openapi.TYPE_STRING, description='Reference number'),
            'entries': openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'account_id': openapi.Schema(type=openapi.TYPE_STRING),
                        'debit': openapi.Schema(type=openapi.TYPE_NUMBER),
                        'credit': openapi.Schema(type=openapi.TYPE_NUMBER),
                    }
                )
            ),
        },
        required=['date', 'description', 'entries']
    ),
    responses={
        201: openapi.Response(description="Manual journal entries created successfully")
    }
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_manual_journal_entry(request):
    """Manually create balancing journal entries (both debits and credits)"""
    try:
        from django.db import transaction
        from decimal import Decimal

        date = request.data.get('date')
        description = request.data.get('description')
        reference = request.data.get('reference', '')
        entries_data = request.data.get('entries', [])

        if not all([date, description, entries_data]):
            return Response({'success': False, 'error': 'date, description, and entries are required'}, status=status.HTTP_400_BAD_REQUEST)

        total_debit = Decimal('0')
        total_credit = Decimal('0')

        # Validate entries and sum debits/credits
        for item in entries_data:
            total_debit += Decimal(str(item.get('debit') or 0))
            total_credit += Decimal(str(item.get('credit') or 0))

        if total_debit != total_credit:
            return Response({'success': False, 'error': f'Debits ({total_debit}) must equal Credits ({total_credit})'}, status=status.HTTP_400_BAD_REQUEST)

        if total_debit == 0:
            return Response({'success': False, 'error': 'Journal entry requires at least a non-zero debit and credit amount'}, status=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(request.user, 'active_tenant', request.user)
        with transaction.atomic():
            created_entries = []
            for item in entries_data:
                try:
                    account = Account.objects.get(id=item.get('account_id'), created_by=tenant)
                except Account.DoesNotExist:
                    raise ValueError(f"Account setup issue: {item.get('account_id')} not found")

                debit = Decimal(str(item.get('debit') or 0))
                credit = Decimal(str(item.get('credit') or 0))

                # Do not insert empty 0 / 0 rows unless absolutely needed
                if debit == 0 and credit == 0:
                    continue

                entry = GeneralLedgerEntry.objects.create(
                    date=date,
                    account=account,
                    debit=debit,
                    credit=credit,
                    description=description,
                    reference=reference,
                    created_by=tenant
                )
                created_entries.append(entry)

        return Response({
            'success': True,
            'message': 'Journal entry created successfully',
            'entries_created': len(created_entries)
        }, status=status.HTTP_201_CREATED)

    except ValueError as ve:
        return Response({'success': False, 'error': str(ve)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Error creating manual journal entry: {str(e)}")
        return Response({'success': False, 'error': 'Internal server error while creating manual entries.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def repair_round_off_entries(request):
    """
    Rebuild ledger entries for all invoices/bills that have a round_off value
    but are missing the corresponding Rounding Off journal entry.
    This fixes the balance sheet imbalance caused by missing round-off entries.
    """
    from billing.models import SalesInvoice, PurchaseBill
    from django.db import transaction as db_transaction
    from decimal import Decimal

    user = request.user
    repaired_invoices = []
    repaired_bills = []
    errors = []

    # Find sales invoices with round_off != 0
    invoices = SalesInvoice.objects.filter(created_by=user).exclude(round_off=0).exclude(round_off=None)
    for invoice in invoices:
        # Check if a rounding_off entry exists for this invoice
        has_round_off_entry = GeneralLedgerEntry.objects.filter(
            sales_invoice=invoice,
            created_by=user,
            account__code='4200',
        ).exists()
        if not has_round_off_entry:
            try:
                with db_transaction.atomic():
                    GeneralLedgerEntry.objects.filter(sales_invoice=invoice).delete()
                    AccountingService.create_sales_invoice_entries(invoice)
                repaired_invoices.append(invoice.invoice_number)
            except Exception as e:
                errors.append(f"Invoice {invoice.invoice_number}: {str(e)}")

    # Find purchase bills with round_off != 0
    bills = PurchaseBill.objects.filter(created_by=user).exclude(round_off=0).exclude(round_off=None)
    for bill in bills:
        has_round_off_entry = GeneralLedgerEntry.objects.filter(
            purchase_bill=bill,
            created_by=user,
            account__code='4200',
        ).exists()
        if not has_round_off_entry:
            try:
                with db_transaction.atomic():
                    GeneralLedgerEntry.objects.filter(purchase_bill=bill).delete()
                    AccountingService.create_purchase_bill_entries(bill)
                repaired_bills.append(bill.bill_number)
            except Exception as e:
                errors.append(f"Bill {bill.bill_number}: {str(e)}")

    return Response({
        'success': True,
        'repaired_invoices': repaired_invoices,
        'repaired_bills': repaired_bills,
        'errors': errors,
        'message': f"Repaired {len(repaired_invoices)} invoice(s) and {len(repaired_bills)} bill(s).",
    })

from datetime import date as date_cls

from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from ledger.bank_deposit import record_bank_deposit, resolve_bank_cash_equity, snapshot_balances
from ledger.models import GeneralLedgerEntry


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bank_deposit_summary(request):
    """Current Cash and Bank balances plus recent bank deposits."""
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        bank, cash, _equity = resolve_bank_cash_equity(tenant)
    except ValidationError as exc:
        return Response({'error': exc.detail}, status=status.HTTP_400_BAD_REQUEST)

    recent = list(
        GeneralLedgerEntry.objects.filter(
            created_by=tenant,
            reference__startswith='BANK-DEPOSIT-',
            debit__gt=0,
        ).select_related('account').order_by('-date', '-created_at')[:20]
    )
    return Response({
        'balances': snapshot_balances(tenant, bank, cash),
        'recent': [
            {
                'id': str(row.id),
                'date': row.date,
                'amount': str(row.debit),
                'description': row.description,
                'reference': row.reference,
                'account': row.account.name,
            }
            for row in recent
        ],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_bank_deposit(request):
    """
    Put money into Bank (1002).
    source=cash  — move collected cash (Dr Bank, Cr Cash)
    source=owner — extra / owner capital (Dr Bank, Cr Owner's Equity)
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    payload = request.data or {}
    try:
        result = record_bank_deposit(
            tenant,
            amount=payload.get('amount'),
            source=payload.get('source'),
            date=payload.get('date') or date_cls.today().isoformat(),
            notes=payload.get('notes') or payload.get('description') or '',
            bank_account_id=payload.get('bank_account_id'),
        )
    except ValidationError as exc:
        detail = exc.detail
        if isinstance(detail, (list, dict)):
            message = detail
        else:
            message = str(detail)
        return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)
    return Response(result, status=status.HTTP_201_CREATED)

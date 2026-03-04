from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.db.models import Q
from datetime import timedelta, datetime
import pandas as pd
import csv
import io
import uuid
from django.core.files.storage import default_storage
from .tasks import process_bank_statement_csv

from .models import BankStatement, BankStatementLine, GeneralLedgerEntry

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_bank_statement(request):
    file_obj = request.FILES.get('file')
    if not file_obj:
        return Response({'error': 'No file provided'}, status=400)

    bank_name = request.data.get('bank_name', 'Unknown Bank')
    account_number = request.data.get('account_number', '')

    # Save file temporarily for Celery worker
    ext = '.csv' if file_obj.name.endswith('.csv') else '.xlsx'
    temp_file_name = f"tmp/bank_statements/{uuid.uuid4()}{ext}"
    saved_path = default_storage.save(temp_file_name, file_obj)

    with transaction.atomic():
        statement = BankStatement.objects.create(
            bank_name=bank_name,
            account_number=account_number,
            file_name=file_obj.name,
            uploaded_by=request.user.active_tenant
        )

    # Dispatch to Celery
    process_bank_statement_csv.delay(statement.id, request.user.id, saved_path)

    return Response({
        'message': 'Statement is being processed in the background.',
        'id': statement.id, 
        'status': 'processing'
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reconciliation_status(request, statement_id):
    try:
        statement = BankStatement.objects.get(pk=statement_id)
    except BankStatement.DoesNotExist:
        return Response({'error': 'Statement not found'}, status=404)

    lines = BankStatementLine.objects.filter(statement=statement).order_by('date')
    
    results = []
    for line in lines:
        match_candidates = []
        if not line.is_reconciled:
            # Look for ledger entries with same amount (+/- 2 days)
            target_amount = line.debit_amount if line.debit_amount > 0 else line.credit_amount
            is_payment = line.debit_amount > 0 

            start_date = line.date - timedelta(days=2)
            end_date = line.date + timedelta(days=2)

            # Look for UNMATCHED entries in that date range
            qs = GeneralLedgerEntry.objects.filter(
                date__range=[start_date, end_date],
                created_by=request.user.active_tenant,
                bank_matches__isnull=True  # Ensure not already matched
            )

            # Bank Debit (Withdrawal) matches Ledger Credit (Asset decrease for Bank/Cash)
            # Bank Credit (Deposit) matches Ledger Debit (Asset increase for Bank/Cash)
            if is_payment:
                qs = qs.filter(credit=target_amount, debit=0)
            else:
                qs = qs.filter(debit=target_amount, credit=0)

            for entry in qs[:5]:
                match_candidates.append({
                    'id': str(entry.id),
                    'date': entry.date,
                    'account': entry.account.name,
                    'description': entry.description,
                    'amount': float(entry.debit if entry.debit > 0 else entry.credit)
                })

        entry_data = None
        if line.matched_entry:
            entry_data = {
                'id': str(line.matched_entry.id),
                'date': line.matched_entry.date,
                'description': line.matched_entry.description
            }

        results.append({
            'id': line.id,
            'date': line.date,
            'description': line.description,
            'debit': float(line.debit_amount),
            'credit': float(line.credit_amount),
            'is_reconciled': line.is_reconciled,
            'matched_entry': entry_data,
            'candidates': match_candidates
        })

    return Response({
        'statement': {
            'id': statement.id, 
            'name': statement.bank_name, 
            'date': statement.uploaded_at
        },
        'lines': results
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reconcile_line(request, line_id):
    try:
        line = BankStatementLine.objects.get(pk=line_id)
    except BankStatementLine.DoesNotExist:
        return Response({'error': 'Line not found'}, status=404)

    entry_id = request.data.get('entry_id')
    if not entry_id:
        return Response({'error': 'Entry ID required'}, status=400)

    try:
        entry = GeneralLedgerEntry.objects.get(pk=entry_id)
    except GeneralLedgerEntry.DoesNotExist:
        return Response({'error': 'Entry not found'}, status=404)

    line.matched_entry = entry
    line.is_reconciled = True
    line.reconciled_at = datetime.now()
    line.save()

    return Response({'status': 'matched'})

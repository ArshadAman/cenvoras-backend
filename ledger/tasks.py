import logging
from celery import shared_task
from django.core.files.storage import default_storage
import pandas as pd
from decimal import Decimal, InvalidOperation
from datetime import datetime
from .models import BankStatement, BankStatementLine
from django.conf import settings

logger = logging.getLogger(__name__)

@shared_task
def process_bank_statement_csv(statement_id, user_id, file_path):
    """
    Parses an uploaded CSV/Excel bank statement in the background.
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)
        statement = BankStatement.objects.get(id=statement_id)
        
        # Read the file
        full_path = default_storage.path(file_path)
        if file_path.endswith('.csv'):
            df = pd.read_csv(full_path)
        else:
            df = pd.read_excel(full_path)
            
        # Standardize column names
        df.columns = [str(c).lower().strip() for c in df.columns]
        
        # Mapping logic
        date_col = next((c for c in df.columns if 'date' in c), None)
        desc_col = next((c for c in df.columns if 'narration' in c or 'description' in c or 'particulars' in c), None)
        debit_col = next((c for c in df.columns if 'withdrawal' in c or 'debit' in c), None)
        credit_col = next((c for c in df.columns if 'deposit' in c or 'credit' in c), None)
        amount_col = next((c for c in df.columns if 'amount' in c and c not in [debit_col, credit_col]), None)
        balance_col = next((c for c in df.columns if 'balance' in c), None)
        
        if not date_col or not desc_col:
            # Cleanup and Error
            default_storage.delete(file_path)
            statement.delete()
            logger.error(f"Failed to find required columns (Date, Description) in statement {statement_id}")
            return False

        lines_created = 0
        
        for index, row in df.iterrows():
            try:
                # Parse Date
                date_val = row[date_col]
                if pd.isna(date_val): continue
                if isinstance(date_val, str):
                    stmt_date = pd.to_datetime(date_val, dayfirst=True).date()
                else:
                    stmt_date = date_val.date()
                
                # Parse Amount
                amount = Decimal('0.00')
                if debit_col and not pd.isna(row[debit_col]) and row[debit_col] != 0:
                    amount = -Decimal(str(row[debit_col]))
                elif credit_col and not pd.isna(row[credit_col]) and row[credit_col] != 0:
                    amount = Decimal(str(row[credit_col]))
                elif amount_col and not pd.isna(row[amount_col]):
                    amount = Decimal(str(row[amount_col]))
                
                if amount == 0: continue
                
                # Parse Balance
                balance = Decimal('0.00')
                if balance_col and not pd.isna(row[balance_col]):
                    balance = Decimal(str(row[balance_col]))
                    
                BankStatementLine.objects.create(
                    statement=statement,
                    date=stmt_date,
                    description=str(row[desc_col])[:500],
                    amount=amount,
                    balance=balance
                )
                lines_created += 1
                
            except Exception as row_error:
                logger.warning(f"Error parsing row {index} in statement {statement_id}: {row_error}")
                continue
                
        # Cleanup temp file
        default_storage.delete(file_path)
        
        logger.info(f"Successfully processed statement {statement_id}. Created {lines_created} lines.")
        return True
        
    except Exception as e:
        logger.error(f"Fatal error processing bank statement CSV task for statement {statement_id}: {e}")
        return False

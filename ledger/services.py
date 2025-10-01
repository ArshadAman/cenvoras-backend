from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from .models import ClientLedgerEntry, JournalEntry, GeneralLedgerEntry, Account, AccountType
from billing.models import SalesInvoice, PurchaseBill


class AccountingService:
    """Service for handling double-entry accounting operations"""
    
    @classmethod
    def get_or_create_default_accounts(cls, user):
        """Get or create default accounting accounts for a user"""
        accounts = {}
        
        # Define default chart of accounts
        default_accounts = [
            # Assets
            ('1001', 'Cash', AccountType.ASSET),
            ('1200', 'Accounts Receivable', AccountType.ASSET),
            ('1300', 'Inventory', AccountType.ASSET),
            ('1400', 'Office Supplies', AccountType.ASSET),
            ('1500', 'Equipment', AccountType.ASSET),
            
            # Liabilities
            ('2001', 'Accounts Payable', AccountType.LIABILITY),
            ('2100', 'Accrued Expenses', AccountType.LIABILITY),
            
            # Equity
            ('3001', 'Owner\'s Equity', AccountType.EQUITY),
            ('3100', 'Retained Earnings', AccountType.EQUITY),
            
            # Revenue
            ('4001', 'Sales Revenue', AccountType.REVENUE),
            ('4100', 'Service Revenue', AccountType.REVENUE),
            
            # Expenses
            ('5001', 'Cost of Goods Sold', AccountType.EXPENSE),
            ('5100', 'Office Supplies Expense', AccountType.EXPENSE),
            ('5200', 'Equipment Expense', AccountType.EXPENSE),
            ('6001', 'Purchases', AccountType.EXPENSE),
        ]
        
        for code, name, account_type in default_accounts:
            account, created = Account.objects.get_or_create(
                code=code,
                created_by=user,
                defaults={
                    'name': name,
                    'account_type': account_type,
                    'description': f'Default {account_type} account'
                }
            )
            # Store by both code and clean name for easy access
            clean_name = name.lower().replace(' ', '_').replace('\'', '')
            accounts[clean_name] = account
            accounts[code] = account
            
        return accounts
    
    @classmethod
    @transaction.atomic
    def create_sales_invoice_entries(cls, sales_invoice):
        """
        Create accounting entries for a sales invoice using double-entry accounting
        
        Journal Entry:
        Dr. Accounts Receivable    [Total Amount]    (Asset increases)
            Cr. Sales Revenue                [Total Amount]    (Revenue increases)
        """
        user = sales_invoice.created_by
        accounts = cls.get_or_create_default_accounts(user)
        
        # Create journal entry
        journal_entry = JournalEntry.objects.create(
            date=sales_invoice.invoice_date,
            description=f"Sales Invoice - {sales_invoice.customer_name or 'Customer'}",
            reference=sales_invoice.invoice_number,
            sales_invoice=sales_invoice,
            created_by=user
        )
        
        # Debit: Accounts Receivable (increase what customer owes)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['accounts_receivable'],
            debit=sales_invoice.total_amount,
            credit=0,
            description=f"Sales to {sales_invoice.customer_name or 'Customer'}"
        )
        
        # Credit: Sales Revenue (record income)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['sales_revenue'],
            debit=0,
            credit=sales_invoice.total_amount,
            description=f"Sales revenue from invoice {sales_invoice.invoice_number}"
        )
        
        # Create/update customer subsidiary ledger entry
        if sales_invoice.customer:
            # Calculate running balance for this customer
            last_entry = ClientLedgerEntry.objects.filter(
                customer=sales_invoice.customer,
                created_by=user
            ).order_by('-date', '-created_at').first()
            
            prev_balance = last_entry.balance if last_entry else Decimal('0')
            new_balance = prev_balance + sales_invoice.total_amount
            
            ClientLedgerEntry.objects.create(
                customer=sales_invoice.customer,
                date=sales_invoice.invoice_date,
                description=f"Sales Invoice {sales_invoice.invoice_number}",
                invoice=sales_invoice,
                journal_entry=journal_entry,
                debit=sales_invoice.total_amount,  # Customer owes us money
                credit=0,
                balance=new_balance,
                created_by=user
            )
        
        return journal_entry
    
    @classmethod
    @transaction.atomic
    def create_purchase_bill_entries(cls, purchase_bill):
        """
        Create accounting entries for a purchase bill using double-entry accounting
        
        Journal Entry:
        Dr. Purchases/Expense     [Total Amount]    (Expense increases)
            Cr. Accounts Payable        [Total Amount]    (Liability increases)
        """
        user = purchase_bill.created_by
        accounts = cls.get_or_create_default_accounts(user)
        
        # Create journal entry
        journal_entry = JournalEntry.objects.create(
            date=purchase_bill.bill_date,
            description=f"Purchase Bill - {purchase_bill.vendor_name}",
            reference=purchase_bill.bill_number,
            purchase_bill=purchase_bill,
            created_by=user
        )
        
        # Debit: Purchases/Expense (record what we bought)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['purchases'],
            debit=purchase_bill.total_amount,
            credit=0,
            description=f"Purchase from {purchase_bill.vendor_name}"
        )
        
        # Credit: Accounts Payable (record what we owe)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['accounts_payable'],
            debit=0,
            credit=purchase_bill.total_amount,
            description=f"Amount owed to {purchase_bill.vendor_name}"
        )
        
        return journal_entry
    
    @classmethod
    @transaction.atomic
    def create_payment_received_entries(cls, customer, amount, description, date, user):
        """
        Create entries when payment is received from customer
        
        Journal Entry:
        Dr. Cash                    [Amount]    (Asset increases)
            Cr. Accounts Receivable      [Amount]    (Asset decreases)
        """
        accounts = cls.get_or_create_default_accounts(user)
        
        # Create journal entry
        journal_entry = JournalEntry.objects.create(
            date=date,
            description=description or f"Payment received from {customer.name}",
            reference="Payment Received",
            created_by=user
        )
        
        # Debit: Cash (increase cash)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['cash'],
            debit=amount,
            credit=0,
            description=f"Payment received from {customer.name}"
        )
        
        # Credit: Accounts Receivable (reduce what customer owes)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['accounts_receivable'],
            debit=0,
            credit=amount,
            description=f"Payment received from {customer.name}"
        )
        
        # Update client subsidiary ledger
        last_entry = ClientLedgerEntry.objects.filter(
            customer=customer,
            created_by=user
        ).order_by('-date', '-created_at').first()
        
        prev_balance = last_entry.balance if last_entry else Decimal('0')
        new_balance = prev_balance - amount  # Reduce what they owe
        
        client_ledger_entry = ClientLedgerEntry.objects.create(
            customer=customer,
            date=date,
            description=description or f"Payment received",
            journal_entry=journal_entry,
            debit=0,
            credit=amount,  # Payment received reduces their debt
            balance=new_balance,
            created_by=user
        )
        
        return client_ledger_entry, journal_entry
    
    @classmethod
    @transaction.atomic
    def create_payment_made_entries(cls, vendor_name, amount, description, date, user):
        """
        Create entries when payment is made to vendor
        
        Journal Entry:
        Dr. Accounts Payable       [Amount]    (Liability decreases)
            Cr. Cash                     [Amount]    (Asset decreases)
        """
        accounts = cls.get_or_create_default_accounts(user)
        
        # Create journal entry
        journal_entry = JournalEntry.objects.create(
            date=date,
            description=description or f"Payment made to {vendor_name}",
            reference="Payment Made",
            created_by=user
        )
        
        # Debit: Accounts Payable (reduce what we owe)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['accounts_payable'],
            debit=amount,
            credit=0,
            description=f"Payment made to {vendor_name}"
        )
        
        # Credit: Cash (reduce cash)
        GeneralLedgerEntry.objects.create(
            journal_entry=journal_entry,
            account=accounts['cash'],
            debit=0,
            credit=amount,
            description=f"Payment made to {vendor_name}"
        )
        
        return journal_entry
    
    @classmethod
    def get_account_balance(cls, account, user):
        """Get the current balance for an account"""
        ledger_entries = GeneralLedgerEntry.objects.filter(
            account=account,
            journal_entry__created_by=user
        )
        
        total_debits = ledger_entries.aggregate(
            total=Sum('debit')
        )['total'] or Decimal('0')
        
        total_credits = ledger_entries.aggregate(
            total=Sum('credit')
        )['total'] or Decimal('0')
        
        # Calculate balance based on account type
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            # For assets and expenses, debit increases balance
            balance = total_debits - total_credits
        else:
            # For liabilities, equity, and revenue, credit increases balance
            balance = total_credits - total_debits
        
        return {
            'debit_total': total_debits,
            'credit_total': total_credits,
            'balance': balance
        }
    
    @classmethod
    def get_trial_balance(cls, user):
        """Get trial balance for all accounts"""
        accounts = Account.objects.filter(created_by=user, is_active=True)
        trial_balance = []
        
        total_debits = Decimal('0')
        total_credits = Decimal('0')
        
        for account in accounts:
            balance_info = cls.get_account_balance(account, user)
            
            # Only include accounts with activity
            if balance_info['debit_total'] > 0 or balance_info['credit_total'] > 0:
                trial_balance.append({
                    'account': account,
                    'debit_total': balance_info['debit_total'],
                    'credit_total': balance_info['credit_total'],
                    'balance': balance_info['balance']
                })
                
                total_debits += balance_info['debit_total']
                total_credits += balance_info['credit_total']
        
        return {
            'accounts': trial_balance,
            'total_debits': total_debits,
            'total_credits': total_credits,
            'is_balanced': total_debits == total_credits
        }
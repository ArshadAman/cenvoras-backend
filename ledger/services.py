from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q
from .models import GeneralLedgerEntry, Account, AccountType
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
        Create detailed accounting entries for a sales invoice using double-entry accounting
        
        Creates one entry for Accounts Receivable (total amount)
        Creates detailed entries for each line item showing actual products sold
        """
        user = sales_invoice.created_by
        accounts = cls.get_or_create_default_accounts(user)
        
        # Get all line items for this invoice
        from billing.models import SalesInvoiceItem
        line_items = SalesInvoiceItem.objects.filter(sales_invoice=sales_invoice)
        
        # Create detailed description with line items
        if line_items.exists():
            item_details = []
            for item in line_items:
                item_desc = f"{item.product.name} (Qty: {item.quantity}"
                if item.unit:
                    item_desc += f" {item.unit}"
                item_desc += f" @ ₹{item.price} = ₹{item.amount})"
                item_details.append(item_desc)
            
            detailed_description = f"Sales to {sales_invoice.customer_name or 'Customer'} - Items: " + "; ".join(item_details)
        else:
            detailed_description = f"Sales to {sales_invoice.customer_name or 'Customer'}"
        
        # Debit: Accounts Receivable (increase what customer owes)
        GeneralLedgerEntry.objects.create(
            date=sales_invoice.invoice_date,
            account=accounts['accounts_receivable'],
            debit=sales_invoice.total_amount,
            credit=0,
            description=detailed_description,
            reference=sales_invoice.invoice_number,
            sales_invoice=sales_invoice,
            created_by=user
        )
        
        # Create detailed credit entries for each line item
        for item in line_items:
            item_description = f"Sale of {item.product.name}"
            if item.quantity > 1:
                item_description += f" (Qty: {item.quantity}"
                if item.unit:
                    item_description += f" {item.unit}"
                item_description += f" @ ₹{item.price})"
            else:
                item_description += f" @ ₹{item.price}"
            
            # Add tax information if applicable
            if item.tax > 0:
                item_description += f" [Tax: ₹{item.tax}]"
            
            # Add discount information if applicable
            if item.discount > 0:
                item_description += f" [Discount: ₹{item.discount}]"
                
            item_description += f" - Invoice {sales_invoice.invoice_number}"
        
            # Credit: Sales Revenue (record income for each item)
            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=accounts['sales_revenue'],
                debit=0,
                credit=item.amount,
                description=item_description,
                reference=f"{sales_invoice.invoice_number}-{item.id}",
                sales_invoice=sales_invoice,
                created_by=user
            )
        
        return True
    
    @classmethod
    @transaction.atomic
    def create_purchase_bill_entries(cls, purchase_bill):
        """
        Create detailed accounting entries for a purchase bill using double-entry accounting
        
        Creates detailed entries for each line item showing actual products purchased
        Creates one entry for Accounts Payable (total amount)
        """
        user = purchase_bill.created_by
        accounts = cls.get_or_create_default_accounts(user)
        
        # Get all line items for this purchase bill
        from billing.models import PurchaseBillItem
        line_items = PurchaseBillItem.objects.filter(purchase_bill=purchase_bill)
        
        # Create detailed debit entries for each line item
        for item in line_items:
            item_description = f"Purchase of {item.product.name}"
            if item.quantity > 1:
                item_description += f" (Qty: {item.quantity}"
                if item.unit:
                    item_description += f" {item.unit}"
                item_description += f" @ ₹{item.price})"
            else:
                item_description += f" @ ₹{item.price}"
            
            # Add tax information if applicable
            if item.tax > 0:
                item_description += f" [Tax: ₹{item.tax}]"
            
            # Add discount information if applicable
            if item.discount > 0:
                item_description += f" [Discount: ₹{item.discount}]"
                
            item_description += f" - Bill {purchase_bill.bill_number}"
        
            # Debit: Purchases/Expense (record what we bought for each item)
            GeneralLedgerEntry.objects.create(
                date=purchase_bill.bill_date,
                account=accounts['purchases'],
                debit=item.amount,
                credit=0,
                description=item_description,
                reference=f"{purchase_bill.bill_number}-{item.id}",
                purchase_bill=purchase_bill,
                created_by=user
            )
        
        # Create detailed description with line items for accounts payable
        if line_items.exists():
            item_details = []
            for item in line_items:
                item_desc = f"{item.product.name} (Qty: {item.quantity}"
                if item.unit:
                    item_desc += f" {item.unit}"
                item_desc += f" @ ₹{item.price} = ₹{item.amount})"
                item_details.append(item_desc)
            
            detailed_payable_description = f"Amount owed to {purchase_bill.vendor_name} - Items: " + "; ".join(item_details)
        else:
            detailed_payable_description = f"Amount owed to {purchase_bill.vendor_name}"
        
        # Credit: Accounts Payable (record what we owe)
        GeneralLedgerEntry.objects.create(
            date=purchase_bill.bill_date,
            account=accounts['accounts_payable'],
            debit=0,
            credit=purchase_bill.total_amount,
            description=detailed_payable_description,
            reference=purchase_bill.bill_number,
            purchase_bill=purchase_bill,
            created_by=user
        )
        
        return True
    
    @classmethod
    @transaction.atomic
    def create_payment_received_entries(cls, customer, amount, description, date, user):
        """
        Create entries when payment is received from customer
        
        Dr. Cash                    [Amount]    (Asset increases)
            Cr. Accounts Receivable [Amount]    (Asset decreases)
        """
        accounts = cls.get_or_create_default_accounts(user)
        
        # Debit: Cash (increase cash)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=accounts['cash'],
            debit=amount,
            credit=0,
            description=f"Payment received from {customer.name if customer else 'Customer'}",
            reference="Payment Received",
            created_by=user
        )
        
        # Credit: Accounts Receivable (reduce what customer owes)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=accounts['accounts_receivable'],
            debit=0,
            credit=amount,
            description=f"Payment received from {customer.name if customer else 'Customer'}",
            reference="Payment Received",
            created_by=user
        )
        
        return True
    
    @classmethod
    @transaction.atomic
    def create_payment_made_entries(cls, vendor_name, amount, description, date, user):
        """
        Create entries when payment is made to vendor
        
        Dr. Accounts Payable    [Amount]    (Liability decreases)
            Cr. Cash            [Amount]    (Asset decreases)
        """
        accounts = cls.get_or_create_default_accounts(user)
        
        # Debit: Accounts Payable (reduce what we owe)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=accounts['accounts_payable'],
            debit=amount,
            credit=0,
            description=f"Payment made to {vendor_name}",
            reference="Payment Made",
            created_by=user
        )
        
        # Credit: Cash (decrease cash)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=accounts['cash'],
            debit=0,
            credit=amount,
            description=f"Payment made to {vendor_name}",
            reference="Payment Made",
            created_by=user
        )
        
        return True
    
    @classmethod
    def get_account_balance(cls, account, user, date_to=None):
        """Get balance for a specific account"""
        entries = GeneralLedgerEntry.objects.filter(
            account=account,
            created_by=user
        )
        
        if date_to:
            entries = entries.filter(date__lte=date_to)
        
        total_debits = entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0')
        total_credits = entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0')
        
        # Calculate balance based on account type
        if account.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            balance = total_debits - total_credits
        else:  # LIABILITY, EQUITY, REVENUE
            balance = total_credits - total_debits
        
        return {
            'debit_total': total_debits,
            'credit_total': total_credits,
            'balance': balance
        }
    
    @classmethod
    def get_trial_balance(cls, user, date_to=None):
        """Get trial balance for all accounts"""
        accounts = Account.objects.filter(created_by=user, is_active=True).order_by('account_type', 'code', 'name')
        
        accounts_data = []
        total_debits = Decimal('0')
        total_credits = Decimal('0')
        
        for account in accounts:
            balance_info = cls.get_account_balance(account, user, date_to)
            accounts_data.append({
                'account': account,
                'debit_total': balance_info['debit_total'],
                'credit_total': balance_info['credit_total'],
                'balance': balance_info['balance']
            })
            total_debits += balance_info['debit_total']
            total_credits += balance_info['credit_total']
        
        return {
            'accounts': accounts_data,
            'total_debits': total_debits,
            'total_credits': total_credits,
            'is_balanced': total_debits == total_credits
        }
    
    @classmethod
    def get_general_ledger_entries(cls, account, user, date_from=None, date_to=None):
        """Get all entries for a specific account"""
        entries = GeneralLedgerEntry.objects.filter(
            account=account,
            created_by=user
        ).order_by('-date', '-created_at')
        
        if date_from:
            entries = entries.filter(date__gte=date_from)
        if date_to:
            entries = entries.filter(date__lte=date_to)
        
        return entries
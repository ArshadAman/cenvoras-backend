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
        user = getattr(user, 'active_tenant', user)
        accounts = {}
        
        # Define default chart of accounts
        default_accounts = [
            # Assets
            ('1001', 'Cash', AccountType.ASSET),
            ('1002', 'Bank', AccountType.ASSET),
            ('1200', 'Accounts Receivable', AccountType.ASSET),
            ('1300', 'Inventory', AccountType.ASSET),
            ('1301', 'Input CGST', AccountType.ASSET),
            ('1302', 'Input SGST', AccountType.ASSET),
            ('1303', 'Input IGST', AccountType.ASSET),
            ('1400', 'Office Supplies', AccountType.ASSET),
            ('1500', 'Equipment', AccountType.ASSET),
            
            # Liabilities
            ('2001', 'Accounts Payable', AccountType.LIABILITY),
            ('2100', 'Accrued Expenses', AccountType.LIABILITY),
            ('2101', 'Customer Advances', AccountType.LIABILITY),
            ('2201', 'Output CGST', AccountType.LIABILITY),
            ('2202', 'Output SGST', AccountType.LIABILITY),
            ('2203', 'Output IGST', AccountType.LIABILITY),
            
            # Equity
            ('3001', 'Owner\'s Equity', AccountType.EQUITY),
            ('3100', 'Retained Earnings', AccountType.EQUITY),
            
            # Revenue
            ('4001', 'Sales Revenue', AccountType.REVENUE),
            ('4100', 'Service Revenue', AccountType.REVENUE),
            ('4200', 'Rounding Off', AccountType.REVENUE),
            
            # Expenses
            ('5001', 'Cost of Goods Sold', AccountType.EXPENSE),
            ('5100', 'Office Supplies Expense', AccountType.EXPENSE),
            ('5200', 'Equipment Expense', AccountType.EXPENSE),
            ('6001', 'Purchases', AccountType.EXPENSE),
        ]
        
        existing_accounts = Account.objects.filter(
            created_by=user,
            code__in=[code for code, _name, _type in default_accounts],
        )
        accounts_by_code = {account.code: account for account in existing_accounts}

        for code, name, account_type in default_accounts:
            account = accounts_by_code.get(code)
            if account is None:
                account, _ = Account.objects.get_or_create(
                    code=code,
                    created_by=user,
                    defaults={
                        'name': name,
                        'account_type': account_type,
                        'description': f'Default {account_type} account',
                    }
                )
                accounts_by_code[code] = account

            # Store by both code and clean name for easy access
            clean_name = name.lower().replace(' ', '_').replace('\'', '')
            accounts[clean_name] = account
            accounts[code] = account

        if 'bank' in accounts:
            accounts['bank_account'] = accounts['bank']
            
        return accounts

    @classmethod
    def _calculate_item_tax_split(cls, item):
        """
        Extract taxable amount and tax amount from a line item.
        Handles SalesInvoiceItem, PurchaseBillItem, CreditNoteItem, DebitNoteItem.
        """
        line_amount = Decimal(str(getattr(item, 'amount', 0) or 0))
        quantity = Decimal(str(getattr(item, 'quantity', 0) or 0))
        price = Decimal(str(getattr(item, 'price', 0) or 0))
        discount = Decimal(str(getattr(item, 'discount', 0) or 0))

        base_amount = quantity * price
        discount_amount = (base_amount * discount) / Decimal('100')
        calc_taxable = (base_amount - discount_amount).quantize(Decimal('0.01'))

        if quantity > 0 and price > 0 and Decimal('0.00') <= calc_taxable <= line_amount:
            taxable_amount = calc_taxable
            tax_amount = line_amount - taxable_amount
        else:
            raw_tax = Decimal(str(getattr(item, 'tax', 0) or 0))
            if raw_tax > 0 and raw_tax < line_amount:
                tax_amount = raw_tax
                taxable_amount = line_amount - tax_amount
            else:
                taxable_amount = line_amount
                tax_amount = Decimal('0.00')

        return taxable_amount, tax_amount

    @classmethod
    @transaction.atomic
    def create_sales_invoice_entries(
        cls,
        sales_invoice,
        accounts_receivable_account=None,
        sales_revenue_account=None,
        **kwargs
    ):
        """
        Create detailed accounting entries for a sales invoice using double-entry accounting.
        
        Dr. Accounts Receivable (Asset)       [Total Amount]
            Cr. Sales Revenue (Revenue)       [Taxable Amount]
            Cr. Output CGST (Liability)       [CGST Amount]
            Cr. Output SGST (Liability)       [SGST Amount]
            (or Cr. Output IGST for Interstate)
            Cr./Dr. Rounding Off              [Round Off Difference]
        """
        user = sales_invoice.created_by
        accounts = cls.get_or_create_default_accounts(user)
        ar_account = accounts_receivable_account or accounts['accounts_receivable']
        sr_account = sales_revenue_account or accounts['sales_revenue']

        from billing.models import SalesInvoiceItem
        line_items = list(SalesInvoiceItem.objects.filter(sales_invoice=sales_invoice).select_related('product'))

        # Build detailed description with line items
        if line_items:
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
            account=ar_account,
            debit=sales_invoice.total_amount,
            credit=0,
            description=detailed_description,
            reference=sales_invoice.invoice_number,
            sales_invoice=sales_invoice,
            customer=sales_invoice.customer,
            created_by=user
        )

        # Determine intra-state vs inter-state
        tenant = getattr(user, 'active_tenant', user)
        seller_state = getattr(tenant, 'state', None) or getattr(user, 'state', None)
        pos = sales_invoice.place_of_supply
        if not pos and sales_invoice.customer:
            pos = getattr(sales_invoice.customer, 'state', None)

        is_interstate = False
        if seller_state and pos:
            is_interstate = (seller_state.strip().upper() != pos.strip().upper())

        total_tax = Decimal('0.00')

        if line_items:
            for item in line_items:
                taxable_amount, tax_amount = cls._calculate_item_tax_split(item)
                total_tax += tax_amount

                item_description = f"Sale of {item.product.name}"
                if item.quantity > 1:
                    item_description += f" (Qty: {item.quantity}"
                    if item.unit:
                        item_description += f" {item.unit}"
                    item_description += f" @ ₹{item.price})"
                else:
                    item_description += f" @ ₹{item.price}"

                if tax_amount > 0:
                    item_description += f" [Tax: ₹{tax_amount}]"
                if item.discount > 0:
                    item_description += f" [Discount: {item.discount}%]"
                item_description += f" - Invoice {sales_invoice.invoice_number}"

                # Credit: Sales Revenue (pure taxable value, excluding GST)
                GeneralLedgerEntry.objects.create(
                    date=sales_invoice.invoice_date,
                    account=sr_account,
                    debit=0,
                    credit=taxable_amount,
                    description=item_description,
                    reference=f"{sales_invoice.invoice_number}-{item.id}",
                    sales_invoice=sales_invoice,
                    customer=None,
                    created_by=user
                )
        else:
            round_off = getattr(sales_invoice, 'round_off', Decimal('0.00')) or Decimal('0.00')
            taxable_amount = sales_invoice.total_amount - round_off
            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=sr_account,
                debit=0,
                credit=taxable_amount,
                description=detailed_description,
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                customer=None,
                created_by=user
            )

        # Credit: Output GST (Duties & Taxes Liability)
        if total_tax > Decimal('0.00'):
            if is_interstate:
                GeneralLedgerEntry.objects.create(
                    date=sales_invoice.invoice_date,
                    account=accounts['output_igst'],
                    debit=0,
                    credit=total_tax,
                    description=f"Output IGST for Invoice {sales_invoice.invoice_number}",
                    reference=sales_invoice.invoice_number,
                    sales_invoice=sales_invoice,
                    customer=None,
                    created_by=user
                )
            else:
                cgst = (total_tax / Decimal('2')).quantize(Decimal('0.01'))
                sgst = total_tax - cgst
                if cgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=sales_invoice.invoice_date,
                        account=accounts['output_cgst'],
                        debit=0,
                        credit=cgst,
                        description=f"Output CGST for Invoice {sales_invoice.invoice_number}",
                        reference=sales_invoice.invoice_number,
                        sales_invoice=sales_invoice,
                        customer=None,
                        created_by=user
                    )
                if sgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=sales_invoice.invoice_date,
                        account=accounts['output_sgst'],
                        debit=0,
                        credit=sgst,
                        description=f"Output SGST for Invoice {sales_invoice.invoice_number}",
                        reference=sales_invoice.invoice_number,
                        sales_invoice=sales_invoice,
                        customer=None,
                        created_by=user
                    )

        # Credit/Debit: Rounding Off (handle difference to keep Balance Sheet balanced)
        round_off = getattr(sales_invoice, 'round_off', Decimal('0.00')) or Decimal('0.00')
        if round_off != 0:
            rounding_off_account = accounts.get('rounding_off')
            if rounding_off_account is None:
                rounding_off_account, _ = Account.objects.get_or_create(
                    code='4200',
                    created_by=user,
                    defaults={
                        'name': 'Rounding Off',
                        'account_type': AccountType.REVENUE,
                        'description': 'Default Revenue account',
                    }
                )
                accounts['rounding_off'] = rounding_off_account
                accounts['4200'] = rounding_off_account

            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=rounding_off_account,
                debit=abs(round_off) if round_off < 0 else 0,
                credit=round_off if round_off > 0 else 0,
                description=f"Rounding off adjustment for Invoice {sales_invoice.invoice_number}",
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                created_by=user
            )

        return True

    @classmethod
    @transaction.atomic
    def create_purchase_bill_entries(
        cls,
        purchase_bill,
        purchases_account=None,
        accounts_payable_account=None,
        **kwargs
    ):
        """
        Create detailed accounting entries for a purchase bill using double-entry accounting.
        
        Dr. Purchases (Expense)                 [Taxable Amount]
        Dr. Input CGST (Asset / Tax Credit)    [CGST Amount]
        Dr. Input SGST (Asset / Tax Credit)    [SGST Amount]
        (or Dr. Input IGST for Interstate)
        Dr./Cr. Rounding Off                    [Round Off Difference]
            Cr. Accounts Payable (Liability)    [Total Amount]
        """
        user = purchase_bill.created_by
        accounts = cls.get_or_create_default_accounts(user)
        pur_account = purchases_account or accounts['purchases']
        ap_account = accounts_payable_account or accounts['accounts_payable']

        from billing.models import PurchaseBillItem
        line_items = list(PurchaseBillItem.objects.filter(purchase_bill=purchase_bill))

        # Determine intra-state vs inter-state
        tenant = getattr(user, 'active_tenant', user)
        buyer_state = getattr(tenant, 'state', None) or getattr(user, 'state', None)
        vendor_state = None
        if purchase_bill.vendor:
            vendor_state = getattr(purchase_bill.vendor, 'state', None)

        is_interstate = False
        if buyer_state and vendor_state:
            is_interstate = (buyer_state.strip().upper() != vendor_state.strip().upper())

        total_tax = Decimal('0.00')

        vendor_obj = getattr(purchase_bill, 'vendor', None)

        if line_items:
            for item in line_items:
                taxable_amount, tax_amount = cls._calculate_item_tax_split(item)
                total_tax += tax_amount

                item_description = f"Purchase of {item.product.name}"
                if item.quantity > 1:
                    item_description += f" (Qty: {item.quantity}"
                    if item.unit:
                        item_description += f" {item.unit}"
                    item_description += f" @ ₹{item.price})"
                else:
                    item_description += f" @ ₹{item.price}"

                if tax_amount > 0:
                    item_description += f" [Tax: ₹{tax_amount}]"
                if item.discount > 0:
                    item_description += f" [Discount: {item.discount}%]"
                item_description += f" - Bill {purchase_bill.bill_number}"

                # Debit: Purchases (pure taxable cost, excluding Input GST)
                GeneralLedgerEntry.objects.create(
                    date=purchase_bill.bill_date,
                    account=pur_account,
                    debit=taxable_amount,
                    credit=0,
                    description=item_description,
                    reference=f"{purchase_bill.bill_number}-{item.id}",
                    purchase_bill=purchase_bill,
                    vendor=vendor_obj,
                    created_by=user
                )
        else:
            round_off = getattr(purchase_bill, 'round_off', Decimal('0.00')) or Decimal('0.00')
            taxable_amount = purchase_bill.total_amount - round_off
            GeneralLedgerEntry.objects.create(
                date=purchase_bill.bill_date,
                account=pur_account,
                debit=taxable_amount,
                credit=0,
                description=f"Purchase from {purchase_bill.vendor_name}",
                reference=purchase_bill.bill_number,
                purchase_bill=purchase_bill,
                vendor=vendor_obj,
                created_by=user
            )

        # Debit: Input GST (Input Tax Credit Asset)
        if total_tax > Decimal('0.00'):
            if is_interstate:
                GeneralLedgerEntry.objects.create(
                    date=purchase_bill.bill_date,
                    account=accounts['input_igst'],
                    debit=total_tax,
                    credit=0,
                    description=f"Input IGST for Bill {purchase_bill.bill_number}",
                    reference=purchase_bill.bill_number,
                    purchase_bill=purchase_bill,
                    vendor=vendor_obj,
                    created_by=user
                )
            else:
                cgst = (total_tax / Decimal('2')).quantize(Decimal('0.01'))
                sgst = total_tax - cgst
                if cgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=purchase_bill.bill_date,
                        account=accounts['input_cgst'],
                        debit=cgst,
                        credit=0,
                        description=f"Input CGST for Bill {purchase_bill.bill_number}",
                        reference=purchase_bill.bill_number,
                        purchase_bill=purchase_bill,
                        vendor=vendor_obj,
                        created_by=user
                    )
                if sgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=purchase_bill.bill_date,
                        account=accounts['input_sgst'],
                        debit=sgst,
                        credit=0,
                        description=f"Input SGST for Bill {purchase_bill.bill_number}",
                        reference=purchase_bill.bill_number,
                        purchase_bill=purchase_bill,
                        vendor=vendor_obj,
                        created_by=user
                    )

        # Create detailed description with line items for accounts payable
        if line_items:
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

        # Credit: Accounts Payable (record total amount owed)
        GeneralLedgerEntry.objects.create(
            date=purchase_bill.bill_date,
            account=ap_account,
            debit=0,
            credit=purchase_bill.total_amount,
            description=detailed_payable_description,
            reference=purchase_bill.bill_number,
            purchase_bill=purchase_bill,
            vendor=vendor_obj,
            created_by=user
        )

        # Debit/Credit: Rounding Off adjustment for purchase bill
        round_off = getattr(purchase_bill, 'round_off', Decimal('0.00')) or Decimal('0.00')
        if round_off != 0:
            rounding_off_account = accounts.get('rounding_off')
            if rounding_off_account is None:
                rounding_off_account, _ = Account.objects.get_or_create(
                    code='4200',
                    created_by=user,
                    defaults={
                        'name': 'Rounding Off',
                        'account_type': AccountType.REVENUE,
                        'description': 'Default Revenue account',
                    }
                )
                accounts['rounding_off'] = rounding_off_account
                accounts['4200'] = rounding_off_account

            GeneralLedgerEntry.objects.create(
                date=purchase_bill.bill_date,
                account=rounding_off_account,
                debit=round_off if round_off > 0 else 0,
                credit=abs(round_off) if round_off < 0 else 0,
                description=f"Rounding off adjustment for Bill {purchase_bill.bill_number}",
                reference=purchase_bill.bill_number,
                purchase_bill=purchase_bill,
                vendor=vendor_obj,
                created_by=user
            )

        return True
    
    @classmethod
    @transaction.atomic
    def create_payment_received_entries(
        cls,
        customer,
        amount,
        description,
        date,
        user,
        invoice=None,
        payment_id=None,
        payment_mode=None,
        payment_account=None,
        **kwargs
    ):
        """
        Create entries when payment is received from customer.
        
        Dr. Cash / Bank             [Amount]    (Asset increases)
            Cr. Accounts Receivable [Amount]    (Asset decreases, for invoice receipts)
            or Cr. Customer Advances [Amount]   (Liability increases, for unlinked receipts)
        """
        accounts = cls.get_or_create_default_accounts(user)
        reference = f"Payment Received {payment_id}" if payment_id else "Payment Received"
        target_account = accounts['accounts_receivable'] if invoice else accounts['customer_advances']

        # Determine receiving asset account (Bank vs Cash)
        mode = (payment_mode or kwargs.get('mode') or '').strip().lower()
        if payment_account:
            receive_account = payment_account
        elif mode in ['upi', 'bank_transfer', 'cheque', 'bank', 'neft', 'rtgs', 'imps', 'card', 'online']:
            receive_account = accounts.get('bank') or accounts.get('bank_account') or accounts.get('1002') or accounts['cash']
        else:
            receive_account = accounts['cash']

        payment_description = description
        if not payment_description:
            mode_display = mode.replace('_', ' ').title() if mode else ''
            via = f" via {mode_display}" if mode_display else ""
        sales_invoice = invoice or kwargs.get('sales_invoice')
        payment_obj = None
        if payment_id:
            from billing.models import Payment
            if isinstance(payment_id, Payment):
                payment_obj = payment_id
            else:
                payment_obj = Payment.objects.filter(id=payment_id).first()

        # Debit: Cash or Bank (increase asset)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=receive_account,
            debit=amount,
            credit=0,
            description=payment_description,
            reference=reference,
            customer=customer,
            sales_invoice=sales_invoice,
            payment=payment_obj,
            created_by=user
        )
        
        # Credit: Accounts Receivable for invoice-linked receipts, otherwise track as customer advance.
        GeneralLedgerEntry.objects.create(
            date=date,
            account=target_account,
            debit=0,
            credit=amount,
            description=payment_description,
            reference=reference,
            customer=customer,
            sales_invoice=sales_invoice,
            payment=payment_obj,
            created_by=user
        )
        
        return True
    
    @classmethod
    @transaction.atomic
    def create_payment_made_entries(
        cls,
        vendor_name,
        amount,
        description,
        date,
        user,
        payment_mode=None,
        payment_account=None,
        purchase_bill=None,
        vendor=None,
        payment=None,
        **kwargs
    ):
        """
        Create entries when payment is made to vendor.
        
        Dr. Accounts Payable    [Amount]    (Liability decreases)
            Cr. Cash / Bank     [Amount]    (Asset decreases)
        """
        accounts = cls.get_or_create_default_accounts(user)
        bill_obj = purchase_bill or kwargs.get('purchase_bill') or kwargs.get('bill')

        # Determine paying asset account (Bank vs Cash)
        mode = (payment_mode or kwargs.get('mode') or '').strip().lower()
        if payment_account:
            paying_account = payment_account
        elif mode in ['upi', 'bank_transfer', 'cheque', 'bank', 'neft', 'rtgs', 'imps', 'card', 'online']:
            paying_account = accounts.get('bank') or accounts.get('bank_account') or accounts.get('1002') or accounts['cash']
        else:
            paying_account = accounts['cash']

        payment_description = description or f"Payment made to {vendor_name}"

        # Resolve vendor
        vendor_obj = vendor or kwargs.get('vendor')
        if not vendor_obj and bill_obj and getattr(bill_obj, 'vendor', None):
            vendor_obj = bill_obj.vendor
        if not vendor_obj and vendor_name:
            from billing.models import Vendor
            vendor_obj = Vendor.objects.filter(name__iexact=vendor_name, created_by=user).first()

        # Resolve payment
        payment_obj = payment or kwargs.get('payment')
        if payment_obj and not hasattr(payment_obj, 'pk'):
            from billing.models import Payment
            payment_obj = Payment.objects.filter(id=payment_obj).first()

        # Debit: Accounts Payable (reduce what we owe)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=accounts['accounts_payable'],
            debit=amount,
            credit=0,
            description=payment_description,
            reference="Payment Made",
            purchase_bill=bill_obj,
            vendor=vendor_obj,
            payment=payment_obj,
            created_by=user
        )
        
        # Credit: Cash or Bank (decrease asset)
        GeneralLedgerEntry.objects.create(
            date=date,
            account=paying_account,
            debit=0,
            credit=amount,
            description=payment_description,
            reference="Payment Made",
            purchase_bill=bill_obj,
            vendor=vendor_obj,
            payment=payment_obj,
            created_by=user
        )
        
        return True
    
    @classmethod
    def get_account_balance(cls, account, user, date_to=None):
        """Get balance for a specific account"""
        user = getattr(user, 'active_tenant', user)
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
        user = getattr(user, 'active_tenant', user)
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
        user = getattr(user, 'active_tenant', user)
        entries = GeneralLedgerEntry.objects.filter(
            account=account,
            created_by=user
        ).order_by('-date', '-created_at')
        
        if date_from:
            entries = entries.filter(date__gte=date_from)
        if date_to:
            entries = entries.filter(date__lte=date_to)
        
        return entries
    @classmethod
    @transaction.atomic
    def create_credit_note_entries(cls, credit_note):
        """
        Create accounting entries for a Sales Return (Credit Note).
        
        Dr. Sales Revenue (Revenue Reversal)     [Taxable Amount]
        Dr. Output CGST (Liability Reversal)    [CGST Amount]
        Dr. Output SGST (Liability Reversal)    [SGST Amount]
        (or Dr. Output IGST for Interstate)
            Cr. Accounts Receivable (Asset)     [Total Amount]
        """
        user = credit_note.created_by
        accounts = cls.get_or_create_default_accounts(user)
        description = f"Sales Return from {credit_note.customer.name} - CN {credit_note.credit_note_number}"
        if credit_note.reason:
            description += f" ({credit_note.get_reason_display()})"

        tenant = getattr(user, 'active_tenant', user)
        seller_state = getattr(tenant, 'state', None) or getattr(user, 'state', None)
        pos = getattr(credit_note.customer, 'state', None)
        if credit_note.original_invoice and getattr(credit_note.original_invoice, 'place_of_supply', None):
            pos = credit_note.original_invoice.place_of_supply

        is_interstate = False
        if seller_state and pos:
            is_interstate = (seller_state.strip().upper() != pos.strip().upper())

        items = list(credit_note.items.all()) if hasattr(credit_note, 'items') else []
        total_tax = Decimal('0.00')
        total_taxable = Decimal('0.00')

        if items:
            for item in items:
                taxable, tax = cls._calculate_item_tax_split(item)
                total_taxable += taxable
                total_tax += tax
        else:
            total_taxable = credit_note.total_amount

        # Debit: Sales Revenue (reduce recorded income)
        GeneralLedgerEntry.objects.create(
            date=credit_note.date,
            account=accounts['sales_revenue'],
            debit=total_taxable,
            credit=0,
            description=description,
            reference=credit_note.credit_note_number,
            credit_note=credit_note,
            customer=credit_note.customer,
            created_by=user
        )

        # Debit: Output GST (reduce tax liability)
        if total_tax > Decimal('0.00'):
            if is_interstate:
                GeneralLedgerEntry.objects.create(
                    date=credit_note.date,
                    account=accounts['output_igst'],
                    debit=total_tax,
                    credit=0,
                    description=f"Output IGST Reversal - CN {credit_note.credit_note_number}",
                    reference=credit_note.credit_note_number,
                    credit_note=credit_note,
                    customer=credit_note.customer,
                    created_by=user
                )
            else:
                cgst = (total_tax / Decimal('2')).quantize(Decimal('0.01'))
                sgst = total_tax - cgst
                if cgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=credit_note.date,
                        account=accounts['output_cgst'],
                        debit=cgst,
                        credit=0,
                        description=f"Output CGST Reversal - CN {credit_note.credit_note_number}",
                        reference=credit_note.credit_note_number,
                        credit_note=credit_note,
                        customer=credit_note.customer,
                        created_by=user
                    )
                if sgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=credit_note.date,
                        account=accounts['output_sgst'],
                        debit=sgst,
                        credit=0,
                        description=f"Output SGST Reversal - CN {credit_note.credit_note_number}",
                        reference=credit_note.credit_note_number,
                        credit_note=credit_note,
                        customer=credit_note.customer,
                        created_by=user
                    )

        # Credit: Accounts Receivable (reduce customer debt)
        GeneralLedgerEntry.objects.create(
            date=credit_note.date,
            account=accounts['accounts_receivable'],
            debit=0,
            credit=credit_note.total_amount,
            description=description,
            reference=credit_note.credit_note_number,
            credit_note=credit_note,
            customer=credit_note.customer,
            created_by=user
        )
        return True

    @classmethod
    @transaction.atomic
    def create_debit_note_entries(cls, debit_note):
        """
        Create accounting entries for a Purchase Return (Debit Note).
        
        Dr. Accounts Payable (Liability)        [Total Amount]
            Cr. Purchases (Expense Reversal)    [Taxable Amount]
            Cr. Input CGST (Asset Reversal)     [CGST Amount]
            Cr. Input SGST (Asset Reversal)     [SGST Amount]
            (or Cr. Input IGST for Interstate)
        """
        user = debit_note.created_by
        accounts = cls.get_or_create_default_accounts(user)
        description = f"Purchase Return to {debit_note.vendor_name} - DN {debit_note.debit_note_number}"
        if debit_note.reason:
            description += f" ({debit_note.get_reason_display()})"

        tenant = getattr(user, 'active_tenant', user)
        buyer_state = getattr(tenant, 'state', None) or getattr(user, 'state', None)
        vendor_state = None
        if debit_note.original_bill and debit_note.original_bill.vendor:
            vendor_state = getattr(debit_note.original_bill.vendor, 'state', None)

        is_interstate = False
        if buyer_state and vendor_state:
            is_interstate = (buyer_state.strip().upper() != vendor_state.strip().upper())

        items = list(debit_note.items.all()) if hasattr(debit_note, 'items') else []
        total_tax = Decimal('0.00')
        total_taxable = Decimal('0.00')

        if items:
            for item in items:
                taxable, tax = cls._calculate_item_tax_split(item)
                total_taxable += taxable
                total_tax += tax
        else:
            total_taxable = debit_note.total_amount

        vendor_obj = getattr(debit_note, 'vendor', None)
        if not vendor_obj and debit_note.original_bill and getattr(debit_note.original_bill, 'vendor', None):
            vendor_obj = debit_note.original_bill.vendor
        if not vendor_obj and getattr(debit_note, 'vendor_name', None):
            from billing.models import Vendor
            vendor_obj = Vendor.objects.filter(name__iexact=debit_note.vendor_name, created_by=user).first()

        # Debit: Accounts Payable (reduce amount owed to vendor)
        GeneralLedgerEntry.objects.create(
            date=debit_note.date,
            account=accounts['accounts_payable'],
            debit=debit_note.total_amount,
            credit=0,
            description=description,
            reference=debit_note.debit_note_number,
            debit_note=debit_note,
            vendor=vendor_obj,
            created_by=user
        )

        # Credit: Purchases (reduce expense)
        GeneralLedgerEntry.objects.create(
            date=debit_note.date,
            account=accounts['purchases'],
            debit=0,
            credit=total_taxable,
            description=description,
            reference=debit_note.debit_note_number,
            debit_note=debit_note,
            vendor=vendor_obj,
            created_by=user
        )

        # Credit: Input GST (reversal of input credit)
        if total_tax > Decimal('0.00'):
            if is_interstate:
                GeneralLedgerEntry.objects.create(
                    date=debit_note.date,
                    account=accounts['input_igst'],
                    debit=0,
                    credit=total_tax,
                    description=f"Input IGST Reversal - DN {debit_note.debit_note_number}",
                    reference=debit_note.debit_note_number,
                    debit_note=debit_note,
                    vendor=vendor_obj,
                    created_by=user
                )
            else:
                cgst = (total_tax / Decimal('2')).quantize(Decimal('0.01'))
                sgst = total_tax - cgst
                if cgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=debit_note.date,
                        account=accounts['input_cgst'],
                        debit=0,
                        credit=cgst,
                        description=f"Input CGST Reversal - DN {debit_note.debit_note_number}",
                        reference=debit_note.debit_note_number,
                        debit_note=debit_note,
                        vendor=vendor_obj,
                        created_by=user
                    )
                if sgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=debit_note.date,
                        account=accounts['input_sgst'],
                        debit=0,
                        credit=sgst,
                        description=f"Input SGST Reversal - DN {debit_note.debit_note_number}",
                        reference=debit_note.debit_note_number,
                        debit_note=debit_note,
                        vendor=vendor_obj,
                        created_by=user
                    )

        return True

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
            ('2102', 'Customer Advances', AccountType.LIABILITY),
            ('2200', 'GST Payable', AccountType.LIABILITY),
            ('2201', 'Output CGST', AccountType.LIABILITY),
            ('2202', 'Output SGST', AccountType.LIABILITY),
            ('2203', 'Output IGST', AccountType.LIABILITY),
            
            # Equity
            ('3001', 'Owner\'s Equity', AccountType.EQUITY),
            ('3100', 'Retained Earnings', AccountType.EQUITY),
            
            # Revenue
            ('4001', 'Sales Revenue', AccountType.REVENUE),
            ('4002', 'Sales Return', AccountType.REVENUE),
            ('4100', 'Service Revenue', AccountType.REVENUE),
            ('4200', 'Rounding Off', AccountType.REVENUE),
            
            # Expenses
            ('5001', 'Cost of Goods Sold', AccountType.EXPENSE),
            ('5100', 'Office Supplies Expense', AccountType.EXPENSE),
            ('5200', 'Equipment Expense', AccountType.EXPENSE),
            ('6001', 'Purchases', AccountType.EXPENSE),
            ('6002', 'Purchase Return', AccountType.EXPENSE),
        ]
        
        existing_accounts = Account.objects.filter(created_by=user)
        accounts_by_code = {account.code: account for account in existing_accounts}
        accounts_by_name = {account.name.strip().lower(): account for account in existing_accounts}

        # Known HR collision mappings: if an HR account was initialized with codes 2200-2205, migrate its code to 2250-2255
        hr_collision_map = {
            'salaries payable': '2250',
            'pf payable': '2251',
            'esi payable': '2252',
            'tds payable': '2253',
            'professional tax payable': '2254',
            'other payroll deductions payable': '2255',
        }

        for code, name, account_type in default_accounts:
            account = accounts_by_name.get(name.strip().lower())
            
            # If account not found by name, check by code
            if account is None:
                acc_by_code = accounts_by_code.get(code)
                if acc_by_code:
                    clean_other = acc_by_code.name.strip().lower()
                    if clean_other == name.strip().lower():
                        account = acc_by_code
                    elif clean_other in hr_collision_map:
                        # Collision: re-assign HR account code to 225x range
                        new_hr_code = hr_collision_map[clean_other]
                        acc_by_code.code = new_hr_code
                        acc_by_code.save(update_fields=['code'])
                        accounts_by_code[new_hr_code] = acc_by_code
                        accounts_by_code.pop(code, None)

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
                accounts_by_name[name.strip().lower()] = account
            elif account.code != code:
                # If found by name but with a non-standard code, and standard code is free, sync it
                if code not in accounts_by_code:
                    accounts_by_code.pop(account.code, None)
                    account.code = code
                    account.save(update_fields=['code'])
                    accounts_by_code[code] = account

            # Store by both code and clean name for easy access
            clean_name = name.lower().replace(' ', '_').replace('\'', '')
            accounts[clean_name] = account
            accounts[code] = account

        if 'bank' in accounts:
            accounts['bank_account'] = accounts['bank']
        accounts['rounding_off'] = accounts.get('rounding_off') or accounts.get('4200')
        accounts['round_off'] = accounts.get('rounding_off') or accounts.get('4200')
        accounts['sales_return'] = accounts.get('sales_return') or accounts.get('4002') or accounts.get('sales_revenue')
        accounts['purchase_return'] = accounts.get('purchase_return') or accounts.get('6002') or accounts.get('purchases')
            
        return accounts

    @classmethod
    def _calculate_item_tax_split(cls, item):
        """
        Extract taxable amount and tax amount from a line item.
        Handles SalesInvoiceItem, PurchaseBillItem, CreditNoteItem, DebitNoteItem.
        CA AUDIT:
        - Round each line's tax to 2 decimal places.
        - Taxable amount = (quantity * price * (1 - discount/100)).quantize(0.01)
        - If tax percentage rate > 0:
            tax_amount = ((taxable_amount * tax_rate) / 100).quantize(0.01)
          else:
            tax_amount = line_amount - taxable_amount (if line_amount > taxable_amount)
        """
        # Keep ledger tax math identical to GSTR reports (incl. absolute-tax legacy rows)
        from billing.gst_utils import calculate_item_tax_breakup
        taxable_amount, tax_amount, _cgst, _sgst, _igst, _rt = calculate_item_tax_breakup(item, is_interstate=False)
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
        # Drafts / non-final invoices must not affect accounting totals
        if getattr(sales_invoice, 'status', None) != 'final':
            return False

        user = sales_invoice.created_by
        accounts = cls.get_or_create_default_accounts(user)
        ar_account = accounts_receivable_account or accounts['accounts_receivable']
        sr_account = sales_revenue_account or accounts['sales_revenue']

        from billing.models import SalesInvoiceItem
        all_items = list(SalesInvoiceItem.objects.filter(sales_invoice=sales_invoice).select_related('product'))
        # Only product items participate in double-entry sales revenue and tax split
        line_items = [
            item for item in all_items 
            if getattr(item, 'row_type', 'item') != 'note' and item.product is not None
        ]

        # Build detailed description with line items
        if line_items:
            item_details = []
            for item in line_items:
                product_name = item.product.name if item.product else (item.description or 'Item')
                item_desc = f"{product_name} (Qty: {item.quantity}"
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

        # Determine intra-state vs inter-state (CA AUDIT: seller state from seller GSTIN first 2 digits)
        from billing.gst_utils import get_seller_state_code, get_pos_state_code
        tenant = getattr(user, 'active_tenant', user)
        seller_state_code = get_seller_state_code(tenant)
        pos = sales_invoice.place_of_supply
        if not pos and sales_invoice.customer:
            pos = getattr(sales_invoice.customer, 'state', None)
        pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
        is_interstate = (seller_state_code != pos_code)

        total_taxable = Decimal('0.00')
        total_cgst = Decimal('0.00')
        total_sgst = Decimal('0.00')
        total_igst = Decimal('0.00')

        if line_items:
            for item in line_items:
                taxable_amount, tax_amount = cls._calculate_item_tax_split(item)
                total_taxable += taxable_amount

                if is_interstate:
                    line_igst = tax_amount
                    total_igst += line_igst
                else:
                    line_cgst = (tax_amount / Decimal('2')).quantize(Decimal('0.01'))
                    line_sgst = tax_amount - line_cgst
                    total_cgst += line_cgst
                    total_sgst += line_sgst

                product_name = item.product.name if item.product else (item.description or 'Item')
                item_description = f"Sale of {product_name}"
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
            total_taxable = taxable_amount
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
        if total_igst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=accounts['output_igst'],
                debit=0,
                credit=total_igst,
                description=f"Output IGST for Invoice {sales_invoice.invoice_number}",
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                customer=None,
                created_by=user
            )
        if total_cgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=accounts['output_cgst'],
                debit=0,
                credit=total_cgst,
                description=f"Output CGST for Invoice {sales_invoice.invoice_number}",
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                customer=None,
                created_by=user
            )
        if total_sgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=sales_invoice.invoice_date,
                account=accounts['output_sgst'],
                debit=0,
                credit=total_sgst,
                description=f"Output SGST for Invoice {sales_invoice.invoice_number}",
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                customer=None,
                created_by=user
            )

        # Credit/Debit: Rounding Off (guarantee Debits == Credits down to the exact cent)
        total_parts = total_taxable + total_cgst + total_sgst + total_igst
        total_debited = Decimal(str(sales_invoice.total_amount or '0.00'))
        round_off_diff = (total_debited - total_parts).quantize(Decimal('0.01'))

        if round_off_diff != Decimal('0.00'):
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
                debit=abs(round_off_diff) if round_off_diff < 0 else 0,
                credit=round_off_diff if round_off_diff > 0 else 0,
                description=f"Rounding off adjustment for Invoice {sales_invoice.invoice_number}",
                reference=sales_invoice.invoice_number,
                sales_invoice=sales_invoice,
                created_by=user
            )

        # Strict Voucher Balance Verification (CA AUDIT: Reject unbalanced vouchers, never create Suspense)
        voucher_entries = GeneralLedgerEntry.objects.filter(sales_invoice=sales_invoice)
        tot_dr = voucher_entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = voucher_entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        if abs(tot_dr - tot_cr) > Decimal('0.001'):
            raise ValueError(f"Unbalanced voucher rejected for Invoice {sales_invoice.invoice_number}: Total Dr ₹{tot_dr} != Total Cr ₹{tot_cr}")

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
        from billing.gst_utils import get_seller_state_code, get_purchase_supplier_state_code
        line_items = list(PurchaseBillItem.objects.filter(purchase_bill=purchase_bill))

        # GSTIN-first POS (same as sales/DN) — empty vendor.state must not force CGST/SGST
        tenant = getattr(user, 'active_tenant', user)
        buyer_code = get_seller_state_code(tenant)
        supplier_code = get_purchase_supplier_state_code(purchase_bill, buyer_code)
        is_interstate = bool(buyer_code and supplier_code and buyer_code != supplier_code)

        total_taxable = Decimal('0.00')
        total_tax = Decimal('0.00')

        vendor_obj = getattr(purchase_bill, 'vendor', None)

        if line_items:
            for item in line_items:
                taxable_amount, tax_amount = cls._calculate_item_tax_split(item)
                total_taxable += taxable_amount
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
            total_taxable = taxable_amount
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

        # Reverse Charge: credit Output GST liability matching Input ITC (GSTR-3B inward RCM)
        is_rcm = bool(getattr(purchase_bill, 'is_reverse_charge', False))
        if is_rcm and total_tax > Decimal('0.00'):
            if is_interstate:
                GeneralLedgerEntry.objects.create(
                    date=purchase_bill.bill_date,
                    account=accounts['output_igst'],
                    debit=0,
                    credit=total_tax,
                    description=f"RCM Output IGST for Bill {purchase_bill.bill_number}",
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
                        account=accounts['output_cgst'],
                        debit=0,
                        credit=cgst,
                        description=f"RCM Output CGST for Bill {purchase_bill.bill_number}",
                        reference=purchase_bill.bill_number,
                        purchase_bill=purchase_bill,
                        vendor=vendor_obj,
                        created_by=user
                    )
                if sgst > 0:
                    GeneralLedgerEntry.objects.create(
                        date=purchase_bill.bill_date,
                        account=accounts['output_sgst'],
                        debit=0,
                        credit=sgst,
                        description=f"RCM Output SGST for Bill {purchase_bill.bill_number}",
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

        # Under RCM, AP is typically the taxable value (GST paid to govt, not vendor)
        ap_credit = (
            total_taxable if is_rcm else Decimal(str(purchase_bill.total_amount or '0.00'))
        ).quantize(Decimal('0.01'))

        # Credit: Accounts Payable (record total amount owed)
        GeneralLedgerEntry.objects.create(
            date=purchase_bill.bill_date,
            account=ap_account,
            debit=0,
            credit=ap_credit,
            description=detailed_payable_description,
            reference=purchase_bill.bill_number,
            purchase_bill=purchase_bill,
            vendor=vendor_obj,
            created_by=user
        )

        # Debit/Credit: Rounding Off adjustment (guarantee Debits == Credits down to the exact cent)
        # RCM: Dr Purchases + Dr Input GST = Cr AP (taxable) + Cr Output GST
        total_debited = total_taxable + total_tax
        total_credited = ap_credit + (total_tax if is_rcm else Decimal('0.00'))
        if not is_rcm:
            total_credited = Decimal(str(purchase_bill.total_amount or '0.00'))
        round_off_diff = (total_credited - total_debited).quantize(Decimal('0.01'))

        if round_off_diff != Decimal('0.00'):
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
                debit=round_off_diff if round_off_diff > 0 else 0,
                credit=abs(round_off_diff) if round_off_diff < 0 else 0,
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
        """Get balance for a specific account (tenant + team entries)."""
        user = getattr(user, 'active_tenant', user)
        entries = GeneralLedgerEntry.objects.filter(
            account=account,
        ).filter(
            Q(created_by=user) | Q(created_by__parent=user)
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
    def purge_non_final_sales_invoice_entries(cls, user):
        """Remove ledger rows incorrectly posted for draft/non-final sales invoices."""
        user = getattr(user, 'active_tenant', user)
        return GeneralLedgerEntry.objects.filter(
            created_by=user,
            sales_invoice__isnull=False,
        ).exclude(sales_invoice__status='final').delete()

    @classmethod
    def get_trial_balance(cls, user, date_to=None):
        """Get trial balance for all accounts"""
        user = getattr(user, 'active_tenant', user)
        cls.purge_non_final_sales_invoice_entries(user)
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
        CA AUDIT (Issue 1):
        Intra-state:
          Dr. Sales Return (Revenue contra)      [Taxable Amount]
          Dr. Output CGST (Liability Reversal)   [CGST Amount]
          Dr. Output SGST (Liability Reversal)   [SGST Amount]
          Cr. Accounts Receivable (Asset)        [Total Amount]
        Inter-state:
          Dr. Sales Return (Revenue contra)      [Taxable Amount]
          Dr. Output IGST (Liability Reversal)   [IGST Amount]
          Cr. Accounts Receivable (Asset)        [Total Amount]
        """
        user = credit_note.created_by
        accounts = cls.get_or_create_default_accounts(user)
        sales_return_acc = accounts.get('sales_return') or accounts.get('4002') or accounts['sales_revenue']

        description = f"Sales Return from {credit_note.customer.name} - CN {credit_note.credit_note_number}"
        if credit_note.reason:
            description += f" ({credit_note.get_reason_display()})"

        from billing.gst_utils import get_seller_state_code, get_pos_state_code
        tenant = getattr(user, 'active_tenant', user)
        seller_state_code = get_seller_state_code(tenant)
        pos = getattr(credit_note.customer, 'state', None)
        if credit_note.original_invoice and getattr(credit_note.original_invoice, 'place_of_supply', None):
            pos = credit_note.original_invoice.place_of_supply
        pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
        is_interstate = (seller_state_code != pos_code)

        items = list(credit_note.items.all()) if hasattr(credit_note, 'items') else []
        total_taxable = Decimal('0.00')
        total_cgst = Decimal('0.00')
        total_sgst = Decimal('0.00')
        total_igst = Decimal('0.00')

        if items:
            for item in items:
                taxable, tax = cls._calculate_item_tax_split(item)
                total_taxable += taxable
                if is_interstate:
                    total_igst += tax
                else:
                    line_cgst = (tax / Decimal('2')).quantize(Decimal('0.01'))
                    line_sgst = tax - line_cgst
                    total_cgst += line_cgst
                    total_sgst += line_sgst
        else:
            total_taxable = Decimal(str(credit_note.total_amount or '0.00'))

        total_parts = total_taxable + total_cgst + total_sgst + total_igst

        # If credit_note was saved with pre-tax amount, synchronize total_amount to gross
        if Decimal(str(credit_note.total_amount or '0.00')) < total_parts and total_parts > Decimal('0.00'):
            old_amt = credit_note.total_amount
            credit_note.total_amount = total_parts
            from billing.models_returns import CreditNote
            CreditNote.objects.filter(pk=credit_note.pk).update(total_amount=total_parts)
            if credit_note.customer:
                from billing.models import Customer
                diff = total_parts - Decimal(str(old_amt or '0.00'))
                Customer.objects.filter(pk=credit_note.customer.pk).update(current_balance=F('current_balance') - diff)

        cn_total = Decimal(str(credit_note.total_amount or '0.00'))

        # Debit: Sales Return (reduce recorded income)
        GeneralLedgerEntry.objects.create(
            date=credit_note.date,
            account=sales_return_acc,
            debit=total_taxable,
            credit=0,
            description=description,
            reference=credit_note.credit_note_number,
            credit_note=credit_note,
            customer=credit_note.customer,
            created_by=user
        )

        # Debit: Output GST (reduce tax liability)
        if total_igst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=credit_note.date,
                account=accounts['output_igst'],
                debit=total_igst,
                credit=0,
                description=f"Output IGST Reversal - CN {credit_note.credit_note_number}",
                reference=credit_note.credit_note_number,
                credit_note=credit_note,
                customer=credit_note.customer,
                created_by=user
            )
        if total_cgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=credit_note.date,
                account=accounts['output_cgst'],
                debit=total_cgst,
                credit=0,
                description=f"Output CGST Reversal - CN {credit_note.credit_note_number}",
                reference=credit_note.credit_note_number,
                credit_note=credit_note,
                customer=credit_note.customer,
                created_by=user
            )
        if total_sgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=credit_note.date,
                account=accounts['output_sgst'],
                debit=total_sgst,
                credit=0,
                description=f"Output SGST Reversal - CN {credit_note.credit_note_number}",
                reference=credit_note.credit_note_number,
                credit_note=credit_note,
                customer=credit_note.customer,
                created_by=user
            )

        # Round Off difference if any
        round_off_diff = (cn_total - total_parts).quantize(Decimal('0.01'))
        if round_off_diff != Decimal('0.00'):
            round_off_acc = accounts.get('rounding_off') or accounts.get('4200')
            GeneralLedgerEntry.objects.create(
                date=credit_note.date,
                account=round_off_acc,
                debit=round_off_diff if round_off_diff > 0 else 0,
                credit=abs(round_off_diff) if round_off_diff < 0 else 0,
                description=f"Rounding off adjustment for CN {credit_note.credit_note_number}",
                reference=credit_note.credit_note_number,
                credit_note=credit_note,
                created_by=user
            )

        # Credit: Accounts Receivable (reduce customer debt)
        GeneralLedgerEntry.objects.create(
            date=credit_note.date,
            account=accounts['accounts_receivable'],
            debit=0,
            credit=cn_total,
            description=description,
            reference=credit_note.credit_note_number,
            credit_note=credit_note,
            customer=credit_note.customer,
            created_by=user
        )

        # Strict Voucher Balance Verification
        voucher_entries = GeneralLedgerEntry.objects.filter(credit_note=credit_note)
        tot_dr = voucher_entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = voucher_entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        if abs(tot_dr - tot_cr) > Decimal('0.001'):
            raise ValueError(f"Unbalanced voucher rejected for Credit Note {credit_note.credit_note_number}: Dr ₹{tot_dr} != Cr ₹{tot_cr}")

        return True

    @classmethod
    @transaction.atomic
    def create_debit_note_entries(cls, debit_note):
        """
        Create accounting entries for a Purchase Return (Debit Note).
        CA AUDIT (Issue 1):
        Intra-state:
          Dr. Accounts Payable (Liability)       [Total Amount]
          Cr. Purchase Return (Expense contra)   [Taxable Amount]
          Cr. Input CGST (Asset Reversal)        [CGST Amount]
          Cr. Input SGST (Asset Reversal)        [SGST Amount]
        Inter-state:
          Dr. Accounts Payable (Liability)       [Total Amount]
          Cr. Purchase Return (Expense contra)   [Taxable Amount]
          Cr. Input IGST (Asset Reversal)        [IGST Amount]
        """
        user = debit_note.created_by
        accounts = cls.get_or_create_default_accounts(user)
        pur_return_acc = accounts.get('purchase_return') or accounts.get('6002') or accounts['purchases']

        description = f"Purchase Return to {debit_note.vendor_name} - DN {debit_note.debit_note_number}"
        if debit_note.reason:
            description += f" ({debit_note.get_reason_display()})"

        from billing.gst_utils import (
            get_seller_state_code,
            get_debit_note_supplier_state_code,
            calculate_item_tax_breakup,
        )
        tenant = getattr(user, 'active_tenant', user)
        buyer_state_code = get_seller_state_code(tenant)
        vendor_pos_code = get_debit_note_supplier_state_code(debit_note, buyer_state_code)
        is_interstate = (buyer_state_code != vendor_pos_code)

        items = list(debit_note.items.all()) if hasattr(debit_note, 'items') else []
        total_taxable = Decimal('0.00')
        total_cgst = Decimal('0.00')
        total_sgst = Decimal('0.00')
        total_igst = Decimal('0.00')

        if items:
            for item in items:
                taxable, _tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_interstate)
                total_taxable += taxable
                total_cgst += cgst
                total_sgst += sgst
                total_igst += igst
        else:
            # Header-only note: treat total_amount as taxable (no GST split available)
            total_taxable = Decimal(str(debit_note.total_amount or '0.00'))

        total_parts = total_taxable + total_cgst + total_sgst + total_igst

        # If debit_note was saved with pre-tax amount, synchronize total_amount to gross
        if Decimal(str(debit_note.total_amount or '0.00')) < total_parts and total_parts > Decimal('0.00'):
            debit_note.total_amount = total_parts
            from billing.models_returns import DebitNote
            DebitNote.objects.filter(pk=debit_note.pk).update(total_amount=total_parts)

        dn_total = Decimal(str(debit_note.total_amount or '0.00'))

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
            debit=dn_total,
            credit=0,
            description=description,
            reference=debit_note.debit_note_number,
            debit_note=debit_note,
            vendor=vendor_obj,
            created_by=user
        )

        # Credit: Purchase Return (reduce expense)
        GeneralLedgerEntry.objects.create(
            date=debit_note.date,
            account=pur_return_acc,
            debit=0,
            credit=total_taxable,
            description=description,
            reference=debit_note.debit_note_number,
            debit_note=debit_note,
            vendor=vendor_obj,
            created_by=user
        )

        # Credit: Input GST (reversal of input credit)
        if total_igst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=debit_note.date,
                account=accounts['input_igst'],
                debit=0,
                credit=total_igst,
                description=f"Input IGST Reversal - DN {debit_note.debit_note_number}",
                reference=debit_note.debit_note_number,
                debit_note=debit_note,
                vendor=vendor_obj,
                created_by=user
            )
        if total_cgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=debit_note.date,
                account=accounts['input_cgst'],
                debit=0,
                credit=total_cgst,
                description=f"Input CGST Reversal - DN {debit_note.debit_note_number}",
                reference=debit_note.debit_note_number,
                debit_note=debit_note,
                vendor=vendor_obj,
                created_by=user
            )
        if total_sgst > Decimal('0.00'):
            GeneralLedgerEntry.objects.create(
                date=debit_note.date,
                account=accounts['input_sgst'],
                debit=0,
                credit=total_sgst,
                description=f"Input SGST Reversal - DN {debit_note.debit_note_number}",
                reference=debit_note.debit_note_number,
                debit_note=debit_note,
                vendor=vendor_obj,
                created_by=user
            )

        # Round Off difference if any
        round_off_diff = (dn_total - total_parts).quantize(Decimal('0.01'))
        if round_off_diff != Decimal('0.00'):
            round_off_acc = accounts.get('rounding_off') or accounts.get('4200')
            GeneralLedgerEntry.objects.create(
                date=debit_note.date,
                account=round_off_acc,
                debit=0 if round_off_diff > 0 else abs(round_off_diff),
                credit=round_off_diff if round_off_diff > 0 else 0,
                description=f"Rounding off adjustment for DN {debit_note.debit_note_number}",
                reference=debit_note.debit_note_number,
                debit_note=debit_note,
                created_by=user
            )

        # Strict Voucher Balance Verification
        voucher_entries = GeneralLedgerEntry.objects.filter(debit_note=debit_note)
        tot_dr = voucher_entries.aggregate(Sum('debit'))['debit__sum'] or Decimal('0.00')
        tot_cr = voucher_entries.aggregate(Sum('credit'))['credit__sum'] or Decimal('0.00')
        if abs(tot_dr - tot_cr) > Decimal('0.001'):
            raise ValueError(f"Unbalanced voucher rejected for Debit Note {debit_note.debit_note_number}: Dr ₹{tot_dr} != Cr ₹{tot_cr}")

        return True

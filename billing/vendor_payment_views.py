"""
Vendor payment, Rule 37 (180-day ITC), and TDS-on-payment endpoints.
"""
from decimal import Decimal
from datetime import date, timedelta

from django.db import transaction
from django.db.models import F
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from billing.models import PurchaseBill
from ledger.models import Account, AccountType, GeneralLedgerEntry
from ledger.services import AccountingService


# Default rates: 194C is 1% (individual/HUF) or 2% (others) — see _tds_rate_for_section.
TDS_SECTION_RATES = {
    '194C': Decimal('2.00'),   # company / non-individual default
    '194J': Decimal('10.00'),  # professional fees
    '194Q': Decimal('0.10'),   # purchase of goods
}


def _bill_taxable_total(bill):
    """GST-exclusive taxable value of a purchase bill (TDS base under 194C/J/Q)."""
    total = Decimal('0.00')
    for item in bill.items.all():
        qty = Decimal(str(getattr(item, 'quantity', 0) or 0))
        price = Decimal(str(getattr(item, 'price', 0) or 0))
        line = (qty * price).quantize(Decimal('0.01'))
        if line <= 0:
            amt = Decimal(str(getattr(item, 'amount', 0) or 0))
            tax_pct = Decimal(str(getattr(item, 'tax', 0) or 0))
            if tax_pct > 0 and amt > 0:
                line = (amt * Decimal('100') / (Decimal('100') + tax_pct)).quantize(Decimal('0.01'))
            else:
                line = amt
        total += line
    return total.quantize(Decimal('0.01'))


def _is_individual_or_huf_vendor(bill):
    """
    194C: 1% for individual/HUF, 2% for others.
    Prefer bill.gst_treatment; else GSTIN 5th character (P=individual, H=HUF).
    """
    treatment = (getattr(bill, 'gst_treatment', None) or '').strip().lower()
    if treatment in ('individual', 'huf', 'unregistered', 'consumer'):
        return True
    if treatment in ('business', 'company', 'regular', 'composition', 'sezu', 'sez'):
        return False
    gstin = (getattr(bill, 'vendor_gstin', None) or '').strip().upper()
    if not gstin and getattr(bill, 'vendor', None):
        gstin = (getattr(bill.vendor, 'gstin', None) or '').strip().upper()
    # GSTIN[2:12]=PAN; PAN 4th char (entity type) is at GSTIN index 5
    if len(gstin) >= 6:
        return gstin[5] in ('P', 'H')
    return False


def _tds_rate_for_section(section, bill):
    section = (section or '').strip().upper()
    if section == '194C':
        return Decimal('1.00') if _is_individual_or_huf_vendor(bill) else Decimal('2.00')
    return TDS_SECTION_RATES.get(section, Decimal('0.00'))


def _compute_tds_preview(bill, amount, tds_section):
    """Return (rate, tds_amount, taxable_base, net_paid) for a payment amount."""
    amount = Decimal(str(amount or 0)).quantize(Decimal('0.01'))
    if not tds_section or amount <= 0:
        return Decimal('0.00'), Decimal('0.00'), Decimal('0.00'), amount
    rate = _tds_rate_for_section(tds_section, bill)
    bill_total = Decimal(str(bill.total_amount or 0))
    taxable_total = _bill_taxable_total(bill)
    if bill_total > 0 and taxable_total > 0:
        tds_base = (amount * taxable_total / bill_total).quantize(Decimal('0.01'))
    else:
        tds_base = amount
    tds_amount = (tds_base * rate / Decimal('100')).quantize(Decimal('0.01'))
    net_paid = (amount - tds_amount).quantize(Decimal('0.01'))
    return rate, tds_amount, tds_base, net_paid


def _is_cash_or_bank_account(account):
    code = str(getattr(account, 'code', '') or '')
    name = (getattr(account, 'name', '') or '').lower()
    if code in ('1001', '1002'):
        return True
    return 'bank' in name or name.strip() == 'cash' or name.startswith('cash ')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unpaid_purchase_bills(request):
    """List unpaid / partially paid purchase bills for vendor payment screen."""
    tenant = getattr(request.user, 'active_tenant', request.user)
    vendor_id = request.query_params.get('vendor')
    qs = PurchaseBill.objects.filter(created_by=tenant).exclude(
        payment_status='paid'
    ).select_related('vendor').order_by('bill_date', 'created_at')
    if vendor_id:
        qs = qs.filter(vendor_id=vendor_id)

    rows = []
    for bill in qs[:200]:
        due = (Decimal(str(bill.total_amount or 0)) - Decimal(str(bill.amount_paid or 0))).quantize(Decimal('0.01'))
        if due <= 0:
            continue
        taxable_total = _bill_taxable_total(bill)
        bill_total = Decimal(str(bill.total_amount or 0))
        if bill_total > 0 and taxable_total > 0:
            taxable_balance = (due * taxable_total / bill_total).quantize(Decimal('0.01'))
        else:
            taxable_balance = due
        rate_194c = _tds_rate_for_section('194C', bill)
        tds_194c = (taxable_balance * rate_194c / Decimal('100')).quantize(Decimal('0.01'))
        rows.append({
            'id': str(bill.id),
            'bill_number': bill.bill_number,
            'bill_date': str(bill.bill_date),
            'vendor_id': str(bill.vendor_id) if bill.vendor_id else None,
            'vendor_name': bill.vendor_name,
            'vendor_gstin': bill.vendor_gstin,
            'gst_treatment': getattr(bill, 'gst_treatment', None),
            'is_individual_or_huf': _is_individual_or_huf_vendor(bill),
            'total_amount': str(bill.total_amount),
            'amount_paid': str(bill.amount_paid),
            'balance_due': str(due),
            'taxable_balance': str(taxable_balance),
            'tds_rate_194c': str(rate_194c),
            'tds_amount_194c': str(tds_194c),
            'net_payable_194c': str((due - tds_194c).quantize(Decimal('0.01'))),
            'payment_status': bill.payment_status,
            'is_reverse_charge': bool(getattr(bill, 'is_reverse_charge', False)),
            'days_outstanding': (date.today() - bill.bill_date).days if bill.bill_date else None,
        })
    return Response({'count': len(rows), 'results': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def vendor_payment_create(request):
    """
    Pay one purchase bill (or amount toward it).
    Body: bill_id, amount, payment_account_id, mode?, tds_section?, tds_amount?, date?
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    bill_id = request.data.get('bill_id') or request.data.get('purchase_bill')
    if not bill_id:
        return Response({'message': 'bill_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        bill = PurchaseBill.objects.select_related('vendor').get(pk=bill_id, created_by=tenant)
    except PurchaseBill.DoesNotExist:
        return Response({'message': 'Purchase bill not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        amount = Decimal(str(request.data.get('amount') or 0)).quantize(Decimal('0.01'))
    except Exception:
        return Response({'message': 'Invalid amount'}, status=status.HTTP_400_BAD_REQUEST)

    due = (Decimal(str(bill.total_amount or 0)) - Decimal(str(bill.amount_paid or 0))).quantize(Decimal('0.01'))
    if amount <= 0:
        return Response({'message': 'amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)
    if amount > due:
        return Response(
            {'message': f'amount exceeds balance due ₹{due}'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    payment_account_id = request.data.get('payment_account_id')
    if not payment_account_id:
        return Response({'message': 'payment_account_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        payment_account = Account.objects.get(id=payment_account_id, created_by=tenant)
    except Account.DoesNotExist:
        return Response({'message': 'payment account not found'}, status=status.HTTP_400_BAD_REQUEST)

    if getattr(payment_account, 'account_type', None) != AccountType.ASSET or not _is_cash_or_bank_account(payment_account):
        return Response(
            {'message': 'Select a Bank or Cash account for vendor payment (receivables and other accounts are not allowed).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    mode = (request.data.get('mode') or 'bank_transfer').strip().lower()
    pay_date = request.data.get('date') or str(date.today())
    tds_section = (request.data.get('tds_section') or '').strip().upper() or None
    try:
        tds_amount = Decimal(str(request.data.get('tds_amount') or 0)).quantize(Decimal('0.01'))
    except Exception:
        tds_amount = Decimal('0.00')

    if tds_section and tds_section not in TDS_SECTION_RATES:
        return Response(
            {'message': f'tds_section must be one of {list(TDS_SECTION_RATES)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    tds_rate = Decimal('0.00')
    if tds_section and tds_amount <= 0:
        tds_rate, tds_amount, _tds_base, _net = _compute_tds_preview(bill, amount, tds_section)
    elif tds_section:
        tds_rate = _tds_rate_for_section(tds_section, bill)
    if tds_amount >= amount:
        return Response({'message': 'tds_amount must be less than payment amount'}, status=status.HTTP_400_BAD_REQUEST)

    net_paid = (amount - tds_amount).quantize(Decimal('0.01'))

    from ledger.balance_checks import assert_sufficient_balance
    try:
        assert_sufficient_balance(payment_account, tenant, net_paid)
    except Exception as exc:
        # DRF ValidationError → 400 with message
        detail = getattr(exc, 'detail', None) or str(exc)
        if isinstance(detail, (list, dict)):
            detail = str(detail)
        return Response({'message': detail}, status=status.HTTP_400_BAD_REQUEST)

    accounts = AccountingService.get_or_create_default_accounts(tenant)

    with transaction.atomic():
        AccountingService.create_payment_made_entries(
            vendor_name=bill.vendor_name,
            amount=net_paid,
            description=f"Vendor payment for {bill.bill_number}",
            date=pay_date,
            user=tenant,
            payment_mode=mode,
            payment_account=payment_account,
            purchase_bill=bill,
            vendor=bill.vendor,
        )

        if tds_amount > 0:
            tds_payable = accounts.get('2253') or accounts.get('tds_payable')
            if tds_payable is None:
                tds_payable, _ = Account.objects.get_or_create(
                    code='2253',
                    created_by=tenant,
                    defaults={
                        'name': 'TDS Payable',
                        'account_type': AccountType.LIABILITY,
                        'description': 'TDS on vendor payments',
                    },
                )
            GeneralLedgerEntry.objects.create(
                date=pay_date,
                account=accounts['accounts_payable'],
                debit=tds_amount,
                credit=0,
                description=f"TDS {tds_section or ''} on payment for {bill.bill_number}".strip(),
                reference=f"TDS-{bill.bill_number}",
                purchase_bill=bill,
                vendor=bill.vendor,
                created_by=tenant,
            )
            GeneralLedgerEntry.objects.create(
                date=pay_date,
                account=tds_payable,
                debit=0,
                credit=tds_amount,
                description=f"TDS {tds_section or ''} payable for {bill.bill_number}".strip(),
                reference=f"TDS-{bill.bill_number}",
                purchase_bill=bill,
                vendor=bill.vendor,
                created_by=tenant,
            )

        PurchaseBill.objects.filter(pk=bill.pk).update(amount_paid=F('amount_paid') + amount)
        bill.refresh_from_db()
        bill.refresh_payment_status(save=True)

        # Restore Rule 37 ITC if previously reversed and now paid
        _restore_rule37_if_needed(bill, tenant, pay_date)

    return Response({
        'message': 'Vendor payment recorded',
        'bill_id': str(bill.id),
        'bill_number': bill.bill_number,
        'amount': str(amount),
        'net_paid': str(net_paid),
        'tds_section': tds_section,
        'tds_rate': str(tds_rate),
        'tds_amount': str(tds_amount),
        'amount_paid': str(bill.amount_paid),
        'payment_status': bill.payment_status,
    }, status=status.HTTP_201_CREATED)


def _restore_rule37_if_needed(bill, tenant, pay_date):
    """If Rule 37 reversal exists for this bill and bill is now paid, reverse the reversal."""
    ref = f"RULE37-REV-{bill.bill_number}"
    existing = GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).exists()
    restore_ref = f"RULE37-RESTORE-{bill.bill_number}"
    if not existing or GeneralLedgerEntry.objects.filter(created_by=tenant, reference=restore_ref).exists():
        return
    if bill.payment_status != 'paid':
        return
    # Flip signs of prior reversal entries by posting opposite
    accounts = AccountingService.get_or_create_default_accounts(tenant)
    revs = list(GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref, purchase_bill=bill))
    for e in revs:
        GeneralLedgerEntry.objects.create(
            date=pay_date,
            account=e.account,
            debit=e.credit,
            credit=e.debit,
            description=f"Rule 37 ITC restore on payment — {bill.bill_number}",
            reference=restore_ref,
            purchase_bill=bill,
            vendor=bill.vendor,
            created_by=tenant,
        )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def rule37_itc_reversals(request):
    """
    GET: list unpaid bills older than 180 days with estimated ITC at risk.
    POST: apply reversals for provided bill_ids (or all due). Body: {bill_ids?: [], dry_run?: bool}
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    cutoff = date.today() - timedelta(days=180)
    candidates = PurchaseBill.objects.filter(
        created_by=tenant,
        bill_date__lte=cutoff,
    ).exclude(payment_status='paid').select_related('vendor')

    from billing.gst_utils import (
        get_seller_state_code,
        get_purchase_supplier_state_code,
        calculate_item_tax_breakup,
    )

    rows = []
    for bill in candidates:
        buyer = get_seller_state_code(tenant)
        supplier = get_purchase_supplier_state_code(bill, buyer)
        interstate = bool(buyer and supplier and buyer != supplier)
        tax_total = Decimal('0.00')
        for item in bill.items.all():
            _t, tax_amt, *_rest = calculate_item_tax_breakup(item, is_interstate=interstate)
            tax_total += tax_amt
        already = GeneralLedgerEntry.objects.filter(
            created_by=tenant, reference=f"RULE37-REV-{bill.bill_number}"
        ).exists()
        restored = GeneralLedgerEntry.objects.filter(
            created_by=tenant, reference=f"RULE37-RESTORE-{bill.bill_number}"
        ).exists()
        if tax_total <= 0:
            continue
        rows.append({
            'id': str(bill.id),
            'bill_number': bill.bill_number,
            'bill_date': str(bill.bill_date),
            'vendor_name': bill.vendor_name,
            'balance_due': str(
                (Decimal(str(bill.total_amount or 0)) - Decimal(str(bill.amount_paid or 0))).quantize(Decimal('0.01'))
            ),
            'itc_at_risk': str(tax_total.quantize(Decimal('0.01'))),
            'already_reversed': already and not restored,
            'days_outstanding': (date.today() - bill.bill_date).days,
        })

    if request.method == 'GET':
        return Response({'cutoff_date': str(cutoff), 'count': len(rows), 'results': rows})

    dry_run = bool(request.data.get('dry_run'))
    bill_ids = request.data.get('bill_ids') or [r['id'] for r in rows if not r['already_reversed']]
    accounts = AccountingService.get_or_create_default_accounts(tenant)
    applied = []

    for bid in bill_ids:
        bill = PurchaseBill.objects.filter(pk=bid, created_by=tenant).first()
        if not bill:
            continue
        ref = f"RULE37-REV-{bill.bill_number}"
        if GeneralLedgerEntry.objects.filter(created_by=tenant, reference=ref).exists():
            continue
        buyer = get_seller_state_code(tenant)
        supplier = get_purchase_supplier_state_code(bill, buyer)
        interstate = bool(buyer and supplier and buyer != supplier)
        tax_total = Decimal('0.00')
        for item in bill.items.all():
            _t, tax_amt, *_rest = calculate_item_tax_breakup(item, is_interstate=interstate)
            tax_total += tax_amt
        if tax_total <= 0:
            continue
        if dry_run:
            applied.append({'bill_number': bill.bill_number, 'itc': str(tax_total), 'dry_run': True})
            continue
        with transaction.atomic():
            # Reverse ITC: Cr Input GST, Dr Purchase / ITC Reversal expense
            expense = accounts.get('purchases')
            if interstate:
                GeneralLedgerEntry.objects.create(
                    date=date.today(), account=accounts['input_igst'],
                    debit=0, credit=tax_total,
                    description=f"Rule 37 ITC reversal — {bill.bill_number}",
                    reference=ref, purchase_bill=bill, vendor=bill.vendor, created_by=tenant,
                )
            else:
                cgst = (tax_total / Decimal('2')).quantize(Decimal('0.01'))
                sgst = tax_total - cgst
                GeneralLedgerEntry.objects.create(
                    date=date.today(), account=accounts['input_cgst'],
                    debit=0, credit=cgst,
                    description=f"Rule 37 ITC reversal CGST — {bill.bill_number}",
                    reference=ref, purchase_bill=bill, vendor=bill.vendor, created_by=tenant,
                )
                GeneralLedgerEntry.objects.create(
                    date=date.today(), account=accounts['input_sgst'],
                    debit=0, credit=sgst,
                    description=f"Rule 37 ITC reversal SGST — {bill.bill_number}",
                    reference=ref, purchase_bill=bill, vendor=bill.vendor, created_by=tenant,
                )
            GeneralLedgerEntry.objects.create(
                date=date.today(), account=expense,
                debit=tax_total, credit=0,
                description=f"Rule 37 ITC reversal expense — {bill.bill_number}",
                reference=ref, purchase_bill=bill, vendor=bill.vendor, created_by=tenant,
            )
        applied.append({'bill_number': bill.bill_number, 'itc': str(tax_total)})

    return Response({'applied': applied, 'count': len(applied)}, status=status.HTTP_200_OK)

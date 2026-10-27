from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Payment
from .serializers import PaymentSerializer
from .balance_sync import recompute_customer_balance, recompute_invoice_amount_paid

class _Reject(Exception):
    def __init__(self, errors):
        super().__init__('rejected')
        self.errors = errors


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ('false', '0', 'no', 'off', '')
    return bool(v)


@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def payment_list_create(request):
    """
    List all payments or create a new payment.
    POST /payments/
    {
        "customer": "UUID",
        "date": "2024-01-01",
        "amount": 5000,
        "mode": "cash"
    }
    """
    if request.method == 'GET':
        tenant = getattr(request.user, 'active_tenant', request.user)
        payments = (Payment.objects.filter(created_by=tenant).select_related('customer', 'invoice')
                    .prefetch_related('adjustments__invoice').order_by('-date', '-created_at'))
        serializer = PaymentSerializer(payments, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        from django.db import transaction
        from decimal import Decimal

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        tenant = getattr(request.user, 'active_tenant', request.user)
        # R18 P0-A: auto-apply only when asked for (plain receipt against dues). A legacy client that sends
        # supply_kind without auto_allocate is recording an advance: never auto-applied.
        if 'auto_allocate' in data:
            auto_allocate = _truthy(data.pop('auto_allocate'))
        else:
            auto_allocate = not (data.get('supply_kind') or '').strip()

        if not data.get('invoice') and data.get('customer') and auto_allocate:
            customer_id = data.get('customer')
            from .models import SalesInvoice
            from .advances import _d
            try:
                receipt_date = _d(data.get('date'))
            except Exception:
                receipt_date = None
            # Only invoices dated on/before the receipt (a receipt before the invoice is an advance).
            open_invoices = [
                inv for inv in SalesInvoice.objects.filter(
                    created_by=tenant, customer_id=customer_id, status='final',
                    **({'invoice_date__lte': receipt_date} if receipt_date else {'pk__in': []}),
                ).order_by('invoice_date', 'created_at')
                if (Decimal(str(inv.total_amount or 0)) - Decimal(str(inv.amount_paid or 0))) > Decimal('0.00')
            ]

            amount = Decimal(str(data.get('amount') or 0))
            if open_invoices and amount > 0:
                created_payments = []
                remaining_amt = amount
                try:
                    with transaction.atomic():
                        for inv in open_invoices:
                            if remaining_amt <= 0:
                                break
                            due = Decimal(str(inv.total_amount or 0)) - Decimal(str(inv.amount_paid or 0))
                            allocated = min(remaining_amt, due)
                            p_data = dict(data)
                            p_data['invoice'] = str(inv.id)
                            p_data['amount'] = str(allocated)
                            p_data['notes'] = (data.get('notes') or f"Payment received for {inv.invoice_number}").strip()
                            serializer = PaymentSerializer(data=p_data, context={'request': request})
                            if not serializer.is_valid():
                                raise _Reject(serializer.errors)
                            created_payments.append(serializer.save())
                            recompute_invoice_amount_paid(inv.id)
                            remaining_amt -= allocated

                        if remaining_amt > 0:
                            # Remainder stays an advance: must be classified by the user (R18, Rudra CA).
                            p_data = dict(data)
                            p_data.pop('invoice', None)
                            p_data['amount'] = str(remaining_amt)
                            p_data['notes'] = (data.get('notes') or "Customer Advance").strip()
                            serializer = PaymentSerializer(data=p_data, context={'request': request})
                            if not serializer.is_valid():
                                errs = dict(serializer.errors)
                                errs['remainder'] = [f'₹{remaining_amt} is not covered by invoices dated on or before '
                                                     f'this receipt and will stay an advance. Classify it first.']
                                raise _Reject(errs)
                            created_payments.append(serializer.save())
                        recompute_customer_balance(customer_id)
                except _Reject as rej:
                    return Response(rej.errors, status=status.HTTP_400_BAD_REQUEST)
                return Response(PaymentSerializer(created_payments[0]).data, status=status.HTTP_201_CREATED)

        serializer = PaymentSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            payment = serializer.save()
            if payment.invoice_id:
                recompute_invoice_amount_paid(payment.invoice_id)
            recompute_customer_balance(payment.customer_id)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def allocate_existing_payment(request, pk):
    """
    Apply an unlinked customer payment (advance) to open final invoices.
    Body: {invoice_id?, amount?, date?}. Never deletes the advance's receipt journals:
    posts an adjusting entry on the application date (see billing.advances).
    """
    from decimal import Decimal
    from .models import SalesInvoice
    from .advances import apply_advance, unapplied_amount, invoice_due
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        payment = Payment.objects.get(pk=pk, created_by=tenant)
    except Payment.DoesNotExist:
        return Response({'error': 'Payment not found'}, status=status.HTTP_404_NOT_FOUND)

    if payment.invoice_id:
        return Response({'message': 'Payment is already linked to an invoice.'}, status=status.HTTP_200_OK)
    if unapplied_amount(payment) <= 0:
        return Response({'message': 'This advance is already fully applied.'}, status=status.HTTP_200_OK)

    target_invoice_id = request.data.get('invoice_id')
    if target_invoice_id:
        target_inv = SalesInvoice.objects.filter(pk=target_invoice_id, created_by=tenant, customer_id=payment.customer_id).first()
        if not target_inv:
            return Response({'error': 'Specified invoice does not belong to this customer.'}, status=status.HTTP_400_BAD_REQUEST)
        open_invoices = [target_inv]
    else:
        open_invoices = [
            inv for inv in SalesInvoice.objects.filter(
                created_by=tenant, customer_id=payment.customer_id, status='final',
            ).order_by('invoice_date', 'created_at')
            if invoice_due(inv) > 0
        ]
    if not open_invoices:
        return Response({'message': 'No open invoices found for this customer to allocate against.'}, status=status.HTTP_200_OK)

    amount = request.data.get('amount')
    try:
        amount = Decimal(str(amount)) if amount not in (None, '') else None
    except Exception:
        return Response({'error': 'Invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)
    on_date = request.data.get('date') or None
    if amount is not None:
        # R18 P1: explicit partial amount, validated server-side.
        if amount <= 0:
            return Response({'error': 'Amount must be greater than 0.'}, status=status.HTTP_400_BAD_REQUEST)
        left = unapplied_amount(payment)
        if amount > left:
            return Response({'error': f'Amount exceeds the unapplied advance (max {left}).'},
                            status=status.HTTP_400_BAD_REQUEST)
        if target_invoice_id:
            due = invoice_due(open_invoices[0])
            if amount > due:
                return Response({'error': f'Amount exceeds invoice {open_invoices[0].invoice_number} '
                                          f'outstanding (max {due}).'}, status=status.HTTP_400_BAD_REQUEST)

    applied = []
    try:
        for inv in open_invoices:
            if amount is not None and amount <= 0:
                break
            adj = apply_advance(payment, inv, amount=amount, on_date=on_date)
            if adj:
                applied.append(adj)
                if amount is not None:
                    amount -= adj.gross
            payment.refresh_from_db()
            if unapplied_amount(payment) <= 0:
                break
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    if not applied:
        return Response({'message': 'Nothing to apply (invoice already paid).'}, status=status.HTTP_200_OK)
    left = unapplied_amount(payment)
    parts = ', '.join(f"₹{a.gross} to {a.invoice.invoice_number}" for a in applied)
    return Response({
        'success': True,
        'message': f"Applied {parts}." + (f" ₹{left} remains as advance." if left > 0 else ''),
        'adjustments': [str(a.id) for a in applied],
        'unapplied': str(left),
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def reverse_advance_adjustment(request, pk):
    """Un-apply an advance from an invoice: mirror entry dated today (or body.date, current month)."""
    from .models import AdvanceAdjustment
    from .advances import reverse_adjustment
    tenant = getattr(request.user, 'active_tenant', request.user)
    adj = AdvanceAdjustment.objects.filter(pk=pk, created_by=tenant).first()
    if not adj:
        return Response({'error': 'Adjustment not found'}, status=status.HTTP_404_NOT_FOUND)
    try:
        reverse_adjustment(adj, on_date=request.data.get('date') or None)
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'success': True, 'message': 'Advance un-applied.'})


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def payment_detail(request, pk):
    """
    Retrieve, update or delete a payment instance.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        payment = Payment.objects.get(pk=pk, created_by=tenant)
    except Payment.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = PaymentSerializer(payment)
        return Response(serializer.data)

    elif request.method == 'PUT':
        if payment.adjustments.filter(reversed_on__isnull=True).exists():
            return Response({'error': 'This advance has been applied to invoices. Un-apply it before editing.'},
                            status=status.HTTP_400_BAD_REQUEST)
        old_customer_id = payment.customer_id
        old_invoice_id = payment.invoice_id
        
        serializer = PaymentSerializer(payment, data=request.data, context={'request': request})
        if serializer.is_valid():
            from django.db import transaction
            
            with transaction.atomic():
                updated_payment = serializer.save()

                if old_invoice_id:
                    recompute_invoice_amount_paid(old_invoice_id)
                if updated_payment.invoice_id and updated_payment.invoice_id != old_invoice_id:
                    recompute_invoice_amount_paid(updated_payment.invoice_id)

                if old_customer_id:
                    recompute_customer_balance(old_customer_id)
                if updated_payment.customer_id and updated_payment.customer_id != old_customer_id:
                    recompute_customer_balance(updated_payment.customer_id)
                elif updated_payment.customer_id:
                    recompute_customer_balance(updated_payment.customer_id)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'DELETE':
        if payment.adjustments.exists():
            return Response({'error': 'This advance has been applied to invoices. Un-apply it instead of deleting; '
                                      'its history is kept for GSTR-1.'}, status=status.HTTP_400_BAD_REQUEST)
        from .advances import is_gstr1_filed
        if not payment.invoice_id and is_gstr1_filed(tenant, payment.date):
            from .serializers import FILED_MONTH_MESSAGE
            return Response({'error': FILED_MONTH_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        # Rely on the post_delete signal in signals.py to revert the customer balance
        deleted_customer_id = payment.customer_id
        deleted_invoice_id = payment.invoice_id
        payment.delete()

        if deleted_invoice_id:
            recompute_invoice_amount_paid(deleted_invoice_id)
        if deleted_customer_id:
            recompute_customer_balance(deleted_customer_id)

        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def advance_defaults(request):
    """
    Suggested classification for an advance from the customer's past final invoices (user must confirm):
    supply_kind = majority of past invoice lines (SAC 99xx = services), gst_rate = most common line rate,
    place_of_supply = customer state. Nulls when nothing can be determined.
    """
    from collections import Counter
    from .models import Customer, SalesInvoiceItem
    tenant = getattr(request.user, 'active_tenant', request.user)
    cust = Customer.objects.filter(pk=request.query_params.get('customer'), created_by=tenant).first()
    if not cust:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    kinds, rates = Counter(), Counter()
    items = (SalesInvoiceItem.objects.filter(sales_invoice__customer=cust, sales_invoice__created_by=tenant,
                                             sales_invoice__status='final').select_related('product'))
    for i in items:
        if getattr(i, 'row_type', 'item') == 'note':
            continue
        h = (i.hsn_sac_code or (i.product.hsn_sac_code if i.product else '') or '').strip()
        if h:
            kinds['services' if h.startswith('99') else 'goods'] += 1
        if i.tax is not None:
            rates[str(i.tax)] += 1
    kind = kinds.most_common(1)[0][0] if kinds else None
    return Response({
        'supply_kind': kind,
        'line_counts': dict(kinds),
        'gst_rate': rates.most_common(1)[0][0] if rates else None,
        'place_of_supply': cust.state or None,
        'needs_confirmation': True,
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def receipt_voucher(request, pk):
    """Rule 50 receipt voucher for a service advance (printable HTML)."""
    from django.http import HttpResponse
    from django.utils.html import escape
    from .gst_rules import advance_tax_split
    from .gst_utils import get_seller_state_code
    from .advances import posted_advance_split
    tenant = getattr(request.user, 'active_tenant', request.user)
    p = Payment.objects.filter(pk=pk, created_by=tenant).select_related('customer').first()
    if not p or not p.receipt_voucher_number:
        return Response({'error': 'No receipt voucher for this payment (only service advances get one).'},
                        status=status.HTTP_404_NOT_FOUND)
    sp = posted_advance_split(p)
    pos = advance_tax_split(p, get_seller_state_code(tenant))['pos']
    seller = escape(getattr(tenant, 'business_name', '') or getattr(tenant, 'company_name', '') or tenant.get_username())
    rows = ''.join(f"<tr><td>{k}</td><td style='text-align:right'>₹{v}</td></tr>" for k, v in (
        ('Taxable value', sp['taxable']), ('CGST', sp['cgst']), ('SGST', sp['sgst']), ('IGST', sp['igst']),
        ('Total received', sp['gross'])))
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(p.receipt_voucher_number)}</title>
<style>body{{font-family:sans-serif;max-width:640px;margin:24px auto}}td{{padding:4px 8px;border-bottom:1px solid #ddd}}</style></head>
<body><h2>Receipt Voucher</h2>
<p><b>{seller}</b><br>GSTIN: {escape(getattr(tenant, 'gstin', '') or '')}</p>
<p>Voucher No: <b>{escape(p.receipt_voucher_number)}</b><br>Date: {p.date:%d/%m/%Y}</p>
<p>Received from: <b>{escape(p.customer.name)}</b><br>Customer GSTIN: {escape(getattr(p.customer, 'gstin', '') or '-')}<br>
Place of supply: {escape(str(pos))}</p>
<p>Description: Advance for services{(' - ' + escape(p.notes)) if p.notes else ''}<br>Rate of tax: {sp['rate']}%</p>
<table>{rows}</table>
<p style="font-size:12px;color:#555">Advance received for supply of services (Rule 50, CGST Rules). Tax payable on reverse charge: No.</p>
<p style="margin-top:48px">Authorised signatory</p></body></html>"""
    return HttpResponse(html, content_type='text/html; charset=utf-8')

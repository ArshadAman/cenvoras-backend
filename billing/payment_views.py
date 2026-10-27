from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Payment
from .serializers import PaymentSerializer
from .balance_sync import recompute_customer_balance, recompute_invoice_amount_paid

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
        auto_allocate = _truthy(data.pop('auto_allocate', True))
        tenant = getattr(request.user, 'active_tenant', request.user)

        # R18 P0-A: a receipt the user records as an advance (supply_kind sent: services/goods) is never
        # auto-applied; it stays an advance (GST at receipt for services) and is applied later through
        # the AdvanceAdjustment flow. FIFO auto-apply is only for plain receipts against existing dues,
        # and only to invoices dated on or before the receipt date (a receipt before the invoice is an advance).
        if (data.get('supply_kind') or '').strip():
            auto_allocate = False

        if not data.get('invoice') and data.get('customer') and auto_allocate:
            customer_id = data.get('customer')
            from .models import SalesInvoice
            from .advances import _d
            try:
                receipt_date = _d(data.get('date'))
            except Exception:
                receipt_date = None
            open_invoices = list(
                SalesInvoice.objects.filter(
                    created_by=tenant,
                    customer_id=customer_id,
                    status='final',
                    **({'invoice_date__lte': receipt_date} if receipt_date else {'pk__in': []}),
                ).order_by('invoice_date', 'created_at')
            )
            # Only consider invoices with actual outstanding balance
            open_invoices = [
                inv for inv in open_invoices
                if (Decimal(str(inv.total_amount or 0)) - Decimal(str(inv.amount_paid or 0))) > Decimal('0.00')
            ]

            amount = Decimal(str(data.get('amount') or 0))
            if open_invoices and amount > 0:
                created_payments = []
                remaining_amt = amount
                with transaction.atomic():
                    for inv in open_invoices:
                        if remaining_amt <= 0:
                            break
                        due = Decimal(str(inv.total_amount or 0)) - Decimal(str(inv.amount_paid or 0))
                        allocated = min(remaining_amt, due)
                        p_data = dict(data)
                        p_data['invoice'] = str(inv.id)
                        p_data['amount'] = float(allocated)
                        p_data['notes'] = (data.get('notes') or f"Payment received for {inv.invoice_number}").strip()
                        serializer = PaymentSerializer(data=p_data, context={'request': request})
                        if serializer.is_valid():
                            p_obj = serializer.save()
                            recompute_invoice_amount_paid(inv.id)
                            created_payments.append(p_obj)
                            remaining_amt -= allocated
                        else:
                            break

                    if remaining_amt > 0:
                        p_data = dict(data)
                        p_data.pop('invoice', None)
                        p_data['amount'] = float(remaining_amt)
                        p_data['notes'] = (data.get('notes') or "Customer Advance").strip()
                        serializer = PaymentSerializer(data=p_data, context={'request': request})
                        if serializer.is_valid():
                            p_obj = serializer.save()
                            created_payments.append(p_obj)

                    recompute_customer_balance(customer_id)

                    if created_payments:
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
            return Response({'error': f"GSTR-1 for {payment.date:%b %Y} is marked filed; this advance was reported "
                                      f"in it and cannot be deleted."}, status=status.HTTP_400_BAD_REQUEST)
        # Rely on the post_delete signal in signals.py to revert the customer balance
        deleted_customer_id = payment.customer_id
        deleted_invoice_id = payment.invoice_id
        payment.delete()

        if deleted_invoice_id:
            recompute_invoice_amount_paid(deleted_invoice_id)
        if deleted_customer_id:
            recompute_customer_balance(deleted_customer_id)

        return Response(status=status.HTTP_204_NO_CONTENT)

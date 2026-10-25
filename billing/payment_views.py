from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Payment
from .serializers import PaymentSerializer
from .balance_sync import recompute_customer_balance, recompute_invoice_amount_paid

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
        payments = Payment.objects.filter(created_by=tenant).order_by('-date', '-created_at')
        serializer = PaymentSerializer(payments, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        from django.db import transaction
        from decimal import Decimal

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        auto_allocate = data.pop('auto_allocate', True)
        tenant = getattr(request.user, 'active_tenant', request.user)

        # If invoice is not explicitly provided, auto_allocate is True, and customer is provided
        if not data.get('invoice') and data.get('customer') and auto_allocate:
            customer_id = data.get('customer')
            from .models import SalesInvoice
            open_invoices = list(
                SalesInvoice.objects.filter(
                    created_by=tenant,
                    customer_id=customer_id,
                    status='final',
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
    Allocate an unlinked customer payment (advance) to open unpaid invoices.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        payment = Payment.objects.get(pk=pk, created_by=tenant)
    except Payment.DoesNotExist:
        return Response({'error': 'Payment not found'}, status=status.HTTP_404_NOT_FOUND)

    if payment.invoice_id:
        return Response({'message': 'Payment is already linked to an invoice.'}, status=status.HTTP_200_OK)

    from .models import SalesInvoice
    from decimal import Decimal
    from django.db import transaction

    target_invoice_id = request.data.get('invoice_id')
    if target_invoice_id:
        target_inv = SalesInvoice.objects.filter(pk=target_invoice_id, created_by=tenant, customer_id=payment.customer_id).first()
        if not target_inv:
            return Response({'error': 'Specified invoice does not belong to this customer.'}, status=status.HTTP_400_BAD_REQUEST)
        open_invoices = [target_inv]
    else:
        open_invoices = list(
            SalesInvoice.objects.filter(
                created_by=tenant,
                customer_id=payment.customer_id,
                status='final',
            ).order_by('invoice_date', 'created_at')
        )
        open_invoices = [
            inv for inv in open_invoices
            if (Decimal(str(inv.total_amount or 0)) - Decimal(str(inv.amount_paid or 0))) > Decimal('0.00')
        ]

    if not open_invoices:
        return Response({'message': 'No open invoices found for this customer to allocate against.'}, status=status.HTTP_200_OK)

    first_inv = open_invoices[0]
    first_due = Decimal(str(first_inv.total_amount or 0)) - Decimal(str(first_inv.amount_paid or 0))

    with transaction.atomic():
        if payment.amount <= first_due:
            payment.invoice = first_inv
            from zoneinfo import ZoneInfo
            from django.utils import timezone
            payment.adjusted_on = timezone.now().astimezone(ZoneInfo('Asia/Kolkata')).date()
            payment.save(update_fields=['invoice', 'adjusted_on'])
            recompute_invoice_amount_paid(first_inv.id)
            recompute_customer_balance(payment.customer_id)
            # Rebuild ledger entry
            from ledger.models import GeneralLedgerEntry
            from ledger.services import AccountingService
            GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}").delete()
            AccountingService.create_payment_received_entries(
                customer=payment.customer,
                amount=payment.amount,
                description=payment.notes or f"Payment received for {first_inv.invoice_number}",
                date=payment.date,
                user=payment.created_by,
                invoice=first_inv,
                payment_id=payment.id,
                payment_mode=payment.mode,
            )
            return Response({
                'success': True,
                'message': f"Allocated payment {payment.id} to invoice {first_inv.invoice_number}."
            })
        else:
            # Payment exceeds first invoice: set first invoice to first_due, and create remaining payment
            remaining_amount = payment.amount - first_due
            payment.amount = first_due
            payment.invoice = first_inv
            from zoneinfo import ZoneInfo
            from django.utils import timezone
            payment.adjusted_on = timezone.now().astimezone(ZoneInfo('Asia/Kolkata')).date()
            payment.save(update_fields=['amount', 'invoice', 'adjusted_on'])
            recompute_invoice_amount_paid(first_inv.id)

            from ledger.models import GeneralLedgerEntry
            from ledger.services import AccountingService
            GeneralLedgerEntry.objects.filter(reference=f"Payment Received {payment.id}").delete()
            AccountingService.create_payment_received_entries(
                customer=payment.customer,
                amount=first_due,
                description=payment.notes or f"Payment received for {first_inv.invoice_number}",
                date=payment.date,
                user=payment.created_by,
                invoice=first_inv,
                payment_id=payment.id,
                payment_mode=payment.mode,
            )

            # Create remaining payment
            Payment.objects.create(
                customer=payment.customer,
                date=payment.date,
                amount=remaining_amount,
                mode=payment.mode,
                reference=payment.reference,
                notes=payment.notes or "Remaining unallocated advance",
                created_by=payment.created_by,
                supply_kind=payment.supply_kind,
            )
            recompute_customer_balance(payment.customer_id)

            return Response({
                'success': True,
                'message': f"Allocated ₹{first_due} to invoice {first_inv.invoice_number}, residual ₹{remaining_amount} remains as advance."
            })

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
        # Rely on the post_delete signal in signals.py to revert the customer balance
        deleted_customer_id = payment.customer_id
        deleted_invoice_id = payment.invoice_id
        payment.delete()

        if deleted_invoice_id:
            recompute_invoice_amount_paid(deleted_invoice_id)
        if deleted_customer_id:
            recompute_customer_balance(deleted_customer_id)

        return Response(status=status.HTTP_204_NO_CONTENT)

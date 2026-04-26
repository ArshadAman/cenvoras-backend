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
        serializer = PaymentSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            payment = serializer.save()
            if payment.invoice_id:
                recompute_invoice_amount_paid(payment.invoice_id)
            recompute_customer_balance(payment.customer_id)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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

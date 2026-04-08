from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Payment, Customer
from .serializers import PaymentSerializer
from django.db.models import Sum

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
        payments = Payment.objects.filter(created_by=request.user).order_by('-date', '-created_at')
        serializer = PaymentSerializer(payments, many=True)
        return Response(serializer.data)

    elif request.method == 'POST':
        serializer = PaymentSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def payment_detail(request, pk):
    """
    Retrieve, update or delete a payment instance.
    """
    try:
        payment = Payment.objects.get(pk=pk, created_by=request.user)
    except Payment.DoesNotExist:
        return Response(status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        serializer = PaymentSerializer(payment)
        return Response(serializer.data)

    elif request.method == 'PUT':
        old_amount = payment.amount
        old_customer_id = payment.customer_id
        
        serializer = PaymentSerializer(payment, data=request.data, context={'request': request})
        if serializer.is_valid():
            from django.db import transaction
            from django.db.models import F
            
            with transaction.atomic():
                updated_payment = serializer.save()
                
                # post_save signal in signals.py only runs logic if `created` is True, 
                # so we must manually calculate and apply the balance difference for edits.
                if old_customer_id == updated_payment.customer_id:
                    amount_diff = old_amount - updated_payment.amount
                    if amount_diff != 0:
                        Customer.objects.filter(pk=updated_payment.customer_id).update(
                            current_balance=F('current_balance') + amount_diff
                        )
                else:
                    # They changed the customer for the payment
                    Customer.objects.filter(pk=old_customer_id).update(
                        current_balance=F('current_balance') + old_amount
                    )
                    Customer.objects.filter(pk=updated_payment.customer_id).update(
                        current_balance=F('current_balance') - updated_payment.amount
                    )
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == 'DELETE':
        # Rely on the post_delete signal in signals.py to revert the customer balance
        payment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

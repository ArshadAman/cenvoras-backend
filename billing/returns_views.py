"""
Views for Sales Returns (Credit Notes) and Purchase Returns (Debit Notes).
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, serializers
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from .models_returns import CreditNote, CreditNoteItem, DebitNote, DebitNoteItem
from .models import Customer
from inventory.models import Product, ProductBatch, StockPoint, Warehouse


# ─── Serializers ──────────────────────────────────────────────

class CreditNoteItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = CreditNoteItem
        fields = ['id', 'product', 'product_name', 'batch', 'hsn_sac_code',
                  'quantity', 'unit', 'price', 'discount', 'tax', 'amount']


class CreditNoteSerializer(serializers.ModelSerializer):
    items = CreditNoteItemSerializer(many=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = CreditNote
        fields = ['id', 'credit_note_number', 'date', 'original_invoice',
                  'customer', 'customer_name', 'reason', 'notes', 'warehouse',
                  'total_amount', 'created_by', 'created_at', 'items']
        read_only_fields = ['id', 'credit_note_number', 'created_by', 'created_at']

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        # Auto-generate credit note number if not provided
        last_note = CreditNote.objects.order_by('-created_at').first()
        if last_note and last_note.credit_note_number.startswith('CN-'):
            try:
                last_num = int(last_note.credit_note_number.split('-')[1])
                validated_data['credit_note_number'] = f"CN-{last_num + 1:04d}"
            except ValueError:
                validated_data['credit_note_number'] = "CN-0001"
        else:
            validated_data['credit_note_number'] = "CN-0001"
            
        credit_note = CreditNote.objects.create(**validated_data)

        user = self.context['request'].user
        target_warehouse = credit_note.warehouse
        if not target_warehouse:
            target_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()

        for item_data in items_data:
            CreditNoteItem.objects.create(credit_note=credit_note, **item_data)

            # Restore stock (returned goods come back in)
            qty = item_data['quantity']
            Product.objects.filter(pk=item_data['product'].pk).update(stock=F('stock') + qty)

            if item_data.get('batch') and target_warehouse:
                sp, _ = StockPoint.objects.get_or_create(
                    batch=item_data['batch'], warehouse=target_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') + qty)

        # Reduce customer balance
        if credit_note.customer:
            Customer.objects.filter(pk=credit_note.customer.pk).update(
                current_balance=F('current_balance') - credit_note.total_amount
            )

        return credit_note


class DebitNoteItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = DebitNoteItem
        fields = ['id', 'product', 'product_name', 'batch', 'hsn_sac_code',
                  'quantity', 'unit', 'price', 'discount', 'tax', 'amount']


class DebitNoteSerializer(serializers.ModelSerializer):
    items = DebitNoteItemSerializer(many=True)

    class Meta:
        model = DebitNote
        fields = ['id', 'debit_note_number', 'date', 'original_bill',
                  'vendor_name', 'vendor_gstin', 'reason', 'notes', 'warehouse',
                  'total_amount', 'created_by', 'created_at', 'items']
        read_only_fields = ['id', 'debit_note_number', 'created_by', 'created_at']

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        # Auto-generate debit note number if not provided
        last_note = DebitNote.objects.order_by('-created_at').first()
        if last_note and last_note.debit_note_number.startswith('DN-'):
            try:
                last_num = int(last_note.debit_note_number.split('-')[1])
                validated_data['debit_note_number'] = f"DN-{last_num + 1:04d}"
            except ValueError:
                validated_data['debit_note_number'] = "DN-0001"
        else:
            validated_data['debit_note_number'] = "DN-0001"
            
        debit_note = DebitNote.objects.create(**validated_data)

        user = self.context['request'].user
        source_warehouse = debit_note.warehouse
        if not source_warehouse:
            source_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()

        for item_data in items_data:
            DebitNoteItem.objects.create(debit_note=debit_note, **item_data)

            # Decrease stock (goods being returned to vendor)
            qty = item_data['quantity']
            Product.objects.filter(pk=item_data['product'].pk).update(
                stock=Greatest(F('stock') - qty, 0)
            )

            if item_data.get('batch') and source_warehouse:
                sp, _ = StockPoint.objects.get_or_create(
                    batch=item_data['batch'], warehouse=source_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - qty)

        return debit_note


# ─── API Views ────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def credit_note_list_create(request):
    if request.method == 'GET':
        notes = CreditNote.objects.filter(created_by=request.user).order_by('-date')
        serializer = CreditNoteSerializer(notes, many=True)
        return Response(serializer.data)

    serializer = CreditNoteSerializer(data=request.data, context={'request': request})
    if not serializer.is_valid():
        print("===== CREDIT NOTE VALIDATION ERROR =====")
        print(serializer.errors)
        print("========================================")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def credit_note_detail(request, pk):
    try:
        note = CreditNote.objects.get(pk=pk, created_by=request.user)
    except CreditNote.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if request.method == 'GET':
        return Response(CreditNoteSerializer(note).data)
    elif request.method == 'DELETE':
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def debit_note_list_create(request):
    if request.method == 'GET':
        notes = DebitNote.objects.filter(created_by=request.user).order_by('-date')
        serializer = DebitNoteSerializer(notes, many=True)
        return Response(serializer.data)

    serializer = DebitNoteSerializer(data=request.data, context={'request': request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def debit_note_detail(request, pk):
    try:
        note = DebitNote.objects.get(pk=pk, created_by=request.user)
    except DebitNote.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if request.method == 'GET':
        return Response(DebitNoteSerializer(note).data)
    elif request.method == 'DELETE':
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

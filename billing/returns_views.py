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
        from decimal import Decimal
        items_data = validated_data.pop('items')
        user = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        validated_data['created_by'] = user
        
        # Clean existing credit note number if provided (prevent double prefix)
        if validated_data.get('credit_note_number'):
            raw_num = validated_data['credit_note_number'].strip()
            while raw_num.startswith('CN-CN-'):
                raw_num = 'CN-' + raw_num[6:]
            validated_data['credit_note_number'] = raw_num
        else:
            existing_numbers = CreditNote.objects.filter(
                created_by=user, credit_note_number__startswith='CN-'
            ).values_list('credit_note_number', flat=True)
            max_num = 0
            for num_str in existing_numbers:
                try:
                    num = int(num_str.split('-')[1])
                    if num > max_num:
                        max_num = num
                except (ValueError, IndexError):
                    continue
            validated_data['credit_note_number'] = f"CN-{max_num + 1:04d}"
            
        # Calculate item line totals and total_amount including GST
        total_gross = Decimal('0.00')
        processed_items = []
        for item_data in items_data:
            qty = Decimal(str(item_data.get('quantity', 0) or 0))
            price = Decimal(str(item_data.get('price', 0) or 0))
            discount = Decimal(str(item_data.get('discount', 0) or 0))
            tax_rate = Decimal(str(item_data.get('tax', 0) or 0))

            base = qty * price
            disc_amt = ((base * discount) / Decimal('100')).quantize(Decimal('0.01'))
            taxable = (base - disc_amt).quantize(Decimal('0.01'))
            tax_amt = ((taxable * tax_rate) / Decimal('100')).quantize(Decimal('0.01')) if tax_rate > 0 else Decimal('0.00')
            line_gross = taxable + tax_amt

            item_data['amount'] = line_gross
            total_gross += line_gross
            processed_items.append(item_data)

        if not validated_data.get('total_amount') or Decimal(str(validated_data['total_amount'])) < total_gross:
            validated_data['total_amount'] = total_gross

        credit_note = CreditNote.objects.create(**validated_data)

        target_warehouse = credit_note.warehouse
        if not target_warehouse:
            target_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()

        for item_data in processed_items:
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

        # Reduce customer balance by full credit note amount (including GST reversal)
        if credit_note.customer:
            Customer.objects.filter(pk=credit_note.customer.pk).update(
                current_balance=F('current_balance') - credit_note.total_amount
            )

        # Create Ledger Entries (Dr Sales Return, Dr Output GST, Cr AR)
        from ledger.services import AccountingService
        AccountingService.create_credit_note_entries(credit_note)

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
        from decimal import Decimal
        items_data = validated_data.pop('items')
        user = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        validated_data['created_by'] = user
        
        # Clean existing debit note number if provided (prevent double prefix)
        if validated_data.get('debit_note_number'):
            raw_num = validated_data['debit_note_number'].strip()
            while raw_num.startswith('DN-DN-'):
                raw_num = 'DN-' + raw_num[6:]
            validated_data['debit_note_number'] = raw_num
        else:
            existing_numbers = DebitNote.objects.filter(
                created_by=user, debit_note_number__startswith='DN-'
            ).values_list('debit_note_number', flat=True)
            max_num = 0
            for num_str in existing_numbers:
                try:
                    num = int(num_str.split('-')[1])
                    if num > max_num:
                        max_num = num
                except (ValueError, IndexError):
                    continue
            validated_data['debit_note_number'] = f"DN-{max_num + 1:04d}"
            
        # Calculate item line totals and total_amount including GST
        total_gross = Decimal('0.00')
        processed_items = []
        for item_data in items_data:
            qty = Decimal(str(item_data.get('quantity', 0) or 0))
            price = Decimal(str(item_data.get('price', 0) or 0))
            discount = Decimal(str(item_data.get('discount', 0) or 0))
            tax_rate = Decimal(str(item_data.get('tax', 0) or 0))

            base = qty * price
            disc_amt = ((base * discount) / Decimal('100')).quantize(Decimal('0.01'))
            taxable = (base - disc_amt).quantize(Decimal('0.01'))
            tax_amt = ((taxable * tax_rate) / Decimal('100')).quantize(Decimal('0.01')) if tax_rate > 0 else Decimal('0.00')
            line_gross = taxable + tax_amt

            item_data['amount'] = line_gross
            total_gross += line_gross
            processed_items.append(item_data)

        if not validated_data.get('total_amount') or Decimal(str(validated_data['total_amount'])) < total_gross:
            validated_data['total_amount'] = total_gross

        # Prefer bill linkage for vendor identity / GSTIN / ledger vendor FK
        original_bill = validated_data.get('original_bill')
        if original_bill:
            if not validated_data.get('vendor_name'):
                validated_data['vendor_name'] = (
                    getattr(original_bill, 'vendor_name', None)
                    or (original_bill.vendor.name if original_bill.vendor_id else '')
                )
            if not validated_data.get('vendor_gstin'):
                validated_data['vendor_gstin'] = (
                    getattr(original_bill, 'vendor_gstin', None)
                    or (getattr(original_bill.vendor, 'gstin', None) if original_bill.vendor_id else None)
                )

        debit_note = DebitNote.objects.create(**validated_data)

        source_warehouse = debit_note.warehouse
        if not source_warehouse:
            source_warehouse = Warehouse.objects.filter(created_by=user, is_active=True).first()

        for item_data in processed_items:
            DebitNoteItem.objects.create(debit_note=debit_note, **item_data)

            # Decrease stock (goods being returned to vendor)
            qty = item_data['quantity']
            Product.objects.filter(pk=item_data['product'].pk).update(
                stock=F('stock') - qty
            )

            if item_data.get('batch') and source_warehouse:
                sp, _ = StockPoint.objects.get_or_create(
                    batch=item_data['batch'], warehouse=source_warehouse,
                    defaults={'quantity': 0}
                )
                StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - qty)

        # Create Ledger Entries (Dr AP, Cr Purchases/Purchase Return, Cr Input GST)
        from ledger.services import AccountingService
        AccountingService.create_debit_note_entries(debit_note)

        return debit_note


# ─── API Views ────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def credit_note_list_create(request):
    tenant = getattr(request.user, 'active_tenant', request.user)
    if request.method == 'GET':
        notes = CreditNote.objects.filter(created_by=tenant).order_by('-date')
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        note = CreditNote.objects.get(pk=pk, created_by=tenant)
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
    tenant = getattr(request.user, 'active_tenant', request.user)
    if request.method == 'GET':
        notes = DebitNote.objects.filter(created_by=tenant).order_by('-date')
        serializer = DebitNoteSerializer(notes, many=True)
        return Response(serializer.data)

    serializer = DebitNoteSerializer(data=request.data, context={'request': request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def debit_note_detail(request, pk):
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        note = DebitNote.objects.get(pk=pk, created_by=tenant)
    except DebitNote.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if request.method == 'GET':
        return Response(DebitNoteSerializer(note).data)
    elif request.method == 'DELETE':
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

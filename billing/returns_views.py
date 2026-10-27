from decimal import Decimal
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
    original_invoice_number = serializers.CharField(source='original_invoice.invoice_number', read_only=True, default=None)
    tax_breakup = serializers.SerializerMethodField()
    section34_warning = serializers.SerializerMethodField()
    without_gst = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = CreditNote
        fields = ['id', 'credit_note_number', 'date', 'original_invoice', 'original_invoice_number',
                  'customer', 'customer_name', 'reason', 'notes', 'warehouse',
                  'total_amount', 'created_by', 'created_at', 'items', 'tax_breakup',
                  'section34_warning', 'gst_time_barred', 'without_gst']
        read_only_fields = ['id', 'credit_note_number', 'created_by', 'created_at', 'section34_warning',
                            'gst_time_barred']
        extra_kwargs = {
            'original_invoice': {'required': True, 'allow_null': False},
        }

    def get_tax_breakup(self, obj):
        """Same computation as GSTR-1 CDNR (billing.gst_utils.credit_note_tax_breakup). Money as strings."""
        from billing.gst_utils import credit_note_tax_breakup
        brk = credit_note_tax_breakup(obj)
        m = lambda d: str(Decimal(d).quantize(Decimal('0.01')))
        return {
            'is_interstate': brk['is_interstate'],
            'pos_code': brk['pos_code'],
            'lines': [
                {'id': str(ln['item'].id), 'taxable': m(ln['taxable']), 'rate': m(ln['rate']),
                 'cgst': m(ln['cgst']), 'sgst': m(ln['sgst']), 'igst': m(ln['igst']), 'tax': m(ln['tax'])}
                for ln in brk['lines']
            ],
            'totals': {k: m(v) for k, v in brk['totals'].items()},
        }

    def get_section34_warning(self, obj):
        from billing.gst_rules import section34_warning
        orig = obj.original_invoice
        if not orig:
            return None
        has_tax = any(Decimal(str(i.tax or 0)) > 0 for i in obj.items.all())
        return section34_warning(orig.invoice_date, obj.date, has_tax)

    def validate_original_invoice(self, value):
        if not value:
            raise serializers.ValidationError('Credit notes must reference the original posted invoice.')
        if getattr(value, 'status', None) != 'final':
            raise serializers.ValidationError('A credit note needs a posted, issued invoice — drafts are not allowed.')
        request = self.context.get('request')
        if request:
            tenant = getattr(request.user, 'active_tenant', request.user)
            if getattr(value, 'created_by_id', None) != getattr(tenant, 'id', None):
                raise serializers.ValidationError('Invoice not found for this business.')
        return value

    def validate(self, attrs):
        from billing.gst_rules import remaining_invoice_returns, section34_warning
        invoice = attrs.get('original_invoice')
        if not invoice:
            raise serializers.ValidationError({
                'original_invoice': 'Credit notes must reference the original posted invoice.'
            })
        items = attrs.get('items') or []
        if not items:
            raise serializers.ValidationError({'items': 'At least one item is required.'})
        # Rudra CA s.34(2): after the deadline a CN can't reduce GST. Offer a commercial CN without GST.
        from billing.gst_rules import section34_deadline
        _nd = attrs.get('date') or getattr(self.instance, 'date', None)
        _dl = section34_deadline(invoice.invoice_date)
        time_barred = bool(_nd and _nd > _dl)
        without_gst = bool(attrs.pop('without_gst', False))
        if without_gst and not time_barred:
            raise serializers.ValidationError({'without_gst': (
                f"A credit note without GST is only for time-barred notes (after {_dl.isoformat()}).")})
        if without_gst:
            for it in items:
                it['tax'] = Decimal('0')
            attrs['gst_time_barred'] = True
        elif time_barred and any(Decimal(str(it.get('tax', 0) or 0)) > 0 for it in items):
            raise serializers.ValidationError({
                'code': 'cn_time_barred',
                'section34': (
                    f"GST can't be reduced: the s.34(2) deadline for invoice {invoice.invoice_number} was "
                    f"{_dl.isoformat()}. Issue it as 'Credit note without GST (time-barred under s.34)'? "
                    f"It will reduce the customer balance and sales value with no output-tax reversal."),
                'offer_without_gst': True,
            })
        remaining = remaining_invoice_returns(invoice)
        this_taxable = Decimal('0.00')
        this_total = Decimal('0.00')
        this_has_tax = False
        qty_by_product = {}
        for item in items:
            product = item.get('product')
            pid = str(getattr(product, 'pk', product))
            qty = Decimal(str(item.get('quantity', 0) or 0))
            qty_by_product[pid] = qty_by_product.get(pid, Decimal('0.00')) + qty
            price = Decimal(str(item.get('price', 0) or 0))
            discount = Decimal(str(item.get('discount', 0) or 0))
            tax_rate = Decimal(str(item.get('tax', 0) or 0))
            base = qty * price
            disc = ((base * discount) / Decimal('100')).quantize(Decimal('0.01'))
            taxable = (base - disc).quantize(Decimal('0.01'))
            tax_amt = ((taxable * tax_rate) / Decimal('100')).quantize(Decimal('0.01')) if tax_rate > 0 else Decimal('0.00')
            this_taxable += taxable
            this_total += taxable + tax_amt
            if tax_rate > 0:
                this_has_tax = True
        if without_gst:
            attrs['total_amount'] = this_total  # commercial CN: no tax component
        for pid, qty in qty_by_product.items():
            allowed = remaining['remaining_qty'].get(pid, Decimal('0.00'))
            if qty > allowed:
                raise serializers.ValidationError({
                    'items': f'Return quantity exceeds remaining quantity on the original invoice ({allowed}).'
                })
        if this_taxable > remaining['remaining_taxable'] + Decimal('0.05'):
            raise serializers.ValidationError({
                'items': 'Credit note taxable value exceeds the remaining value of the original invoice.'
            })
        if this_total + remaining['used_total'] > remaining['invoice_total'] + Decimal('0.05'):
            raise serializers.ValidationError({
                'items': 'Total credit notes against this invoice cannot exceed the invoice value.'
            })
        note_date = attrs.get('date') or getattr(self.instance, 'date', None)
        warning = section34_warning(invoice.invoice_date, note_date, this_has_tax)
        attrs['_section34_warning'] = warning
        return attrs

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        extra = getattr(self, '_section34_warning', None)
        if extra and not ret.get('section34_warning'):
            ret['section34_warning'] = extra
        return ret

    @transaction.atomic
    def create(self, validated_data):
        self._section34_warning = validated_data.pop('_section34_warning', None)
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
    original_bill_number = serializers.CharField(source='original_bill.bill_number', read_only=True, default=None)

    class Meta:
        model = DebitNote
        fields = ['id', 'debit_note_number', 'date', 'original_bill', 'original_bill_number',
                  'vendor_name', 'vendor_gstin', 'reason', 'notes', 'warehouse',
                  'total_amount', 'created_by', 'created_at', 'items']
        read_only_fields = ['id', 'debit_note_number', 'created_by', 'created_at']
        extra_kwargs = {
            'original_bill': {'required': True, 'allow_null': False},
        }

    def validate_original_bill(self, value):
        if not value:
            raise serializers.ValidationError(
                'Debit notes must reference an original purchase bill.'
            )
        request = self.context.get('request')
        if request:
            tenant = getattr(request.user, 'active_tenant', request.user)
            if getattr(value, 'created_by_id', None) != getattr(tenant, 'id', None):
                raise serializers.ValidationError('Purchase bill not found for this business.')
        return value

    def validate(self, attrs):
        from billing.gst_rules import remaining_bill_returns
        bill = attrs.get('original_bill')
        if not bill:
            raise serializers.ValidationError({
                'original_bill': 'Debit notes must reference an original purchase bill.'
            })
        items = attrs.get('items') or []
        if not items:
            raise serializers.ValidationError({
                'items': 'Header-only debit notes are not allowed. Add lines from the original bill.'
            })
        remaining = remaining_bill_returns(bill)
        this_taxable = Decimal('0.00')
        this_tax = Decimal('0.00')
        qty_by_product = {}
        for item in items:
            product = item.get('product')
            pid = str(getattr(product, 'pk', product))
            qty = Decimal(str(item.get('quantity', 0) or 0))
            qty_by_product[pid] = qty_by_product.get(pid, Decimal('0.00')) + qty
            price = Decimal(str(item.get('price', 0) or 0))
            discount = Decimal(str(item.get('discount', 0) or 0))
            tax_rate = Decimal(str(item.get('tax', 0) or 0))
            base = qty * price
            disc = ((base * discount) / Decimal('100')).quantize(Decimal('0.01'))
            taxable = (base - disc).quantize(Decimal('0.01'))
            tax_amt = ((taxable * tax_rate) / Decimal('100')).quantize(Decimal('0.01')) if tax_rate > 0 else Decimal('0.00')
            this_taxable += taxable
            this_tax += tax_amt
        for pid, qty in qty_by_product.items():
            allowed = remaining['remaining_qty'].get(pid, Decimal('0.00'))
            if qty > allowed:
                raise serializers.ValidationError({
                    'items': f'Return quantity exceeds remaining quantity on the original bill ({allowed}).'
                })
        if this_taxable > remaining['remaining_taxable'] + Decimal('0.05'):
            raise serializers.ValidationError({
                'items': 'Debit note taxable value exceeds remaining purchases on the original bill.'
            })
        if this_tax > remaining['remaining_tax'] + Decimal('0.05'):
            raise serializers.ValidationError({
                'items': 'Debit note GST reversal exceeds remaining ITC on the original bill.'
            })
        return attrs

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

def _remaining_payload(remaining):
    q = lambda d: str(Decimal(d).quantize(Decimal('0.01')))
    return {
        'remaining_qty': {pid: q(max(v, Decimal('0'))) for pid, v in remaining['remaining_qty'].items()},
        'remaining_taxable': q(remaining['remaining_taxable']),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def debit_note_remaining(request):
    """Qty still debitable per product id on a purchase bill (same rule the DN serializer enforces)."""
    from billing.models import PurchaseBill
    from billing.gst_rules import remaining_bill_returns
    tenant = getattr(request.user, 'active_tenant', request.user)
    bill = PurchaseBill.objects.filter(pk=request.query_params.get('bill'), created_by=tenant).first() \
        if _is_uuid(request.query_params.get('bill')) else None
    if not bill:
        return Response({'error': 'Purchase bill not found'}, status=404)
    return Response(_remaining_payload(remaining_bill_returns(bill)))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def credit_note_remaining(request):
    """Qty still creditable per product id on a sales invoice (same rule the CN serializer enforces)."""
    from billing.models import SalesInvoice
    from billing.gst_rules import remaining_invoice_returns
    tenant = getattr(request.user, 'active_tenant', request.user)
    inv = SalesInvoice.objects.filter(pk=request.query_params.get('invoice'), created_by=tenant).first() \
        if _is_uuid(request.query_params.get('invoice')) else None
    if not inv:
        return Response({'error': 'Invoice not found'}, status=404)
    return Response(_remaining_payload(remaining_invoice_returns(inv)))


def _is_uuid(v):
    import uuid
    try:
        uuid.UUID(str(v))
        return True
    except (ValueError, TypeError):
        return False


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def credit_note_list_create(request):
    tenant = getattr(request.user, 'active_tenant', request.user)
    if request.method == 'GET':
        notes = (CreditNote.objects.filter(created_by=tenant)
                 .select_related('customer', 'original_invoice', 'created_by')
                 .prefetch_related('items__product').order_by('-date'))
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
        note = (CreditNote.objects.select_related('customer', 'original_invoice', 'created_by')
                .prefetch_related('items__product').get(pk=pk, created_by=tenant))
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

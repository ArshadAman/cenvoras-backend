from billing.uqc import QuantityAsNumberMixin, UQCQuantityMixin
from billing.uqc import to_qty
import re
from rest_framework import serializers
from .models import Product, Warehouse, StockPoint, StockTransfer, StockTransferItem, ProductBatch, ProductMeta, BillOfMaterial, StockJournal, StockJournalItem
from .models_sidecar import ProductMeta, BillOfMaterial, StockJournal
from .serializers_sidecar import ProductMetaSerializer, BillOfMaterialSerializer, StockJournalSerializer

class ProductSerializer(QuantityAsNumberMixin, serializers.ModelSerializer):
    unit = serializers.CharField(max_length=50, required=False, allow_blank=True, default='pcs')
    cost_price = serializers.DecimalField(source='price', max_digits=14, decimal_places=4, required=False, allow_null=True)
    sale_price = serializers.DecimalField(max_digits=14, decimal_places=4, required=False)
    current_stock = serializers.DecimalField(source='stock', max_digits=15, decimal_places=3, read_only=True)
    meta = ProductMetaSerializer(required=False)
    
    class Meta:
        model = Product
        fields = [
            'id', 'item_code', 'name', 'hsn_sac_code', 'description', 'manufacturer', 'internal_reference', 'tax', 'stock', 'current_stock', 'unit',
            'secondary_unit', 'conversion_factor',
            'cost_price', 'price', 'sale_price', 'warranty_months', 'low_stock_alert', 'is_active', 'created_by',
            'created_at', 'updated_at', 'meta'
        ]
        read_only_fields = ['id', 'created_by', 'price', 'current_stock', 'created_at', 'updated_at']

    def validate(self, attrs):
        sale_price = attrs.get('sale_price')
        if sale_price is None and self.instance is not None:
            sale_price = self.instance.sale_price

        if sale_price in (None, ''):
            raise serializers.ValidationError({'sale_price': 'Sale price is required.'})

        # Validate HSN/SAC via shared helper (same messages as invoice/purchase)
        hsn_sac = attrs.get('hsn_sac_code')
        if hsn_sac is None and self.instance is not None:
            hsn_sac = self.instance.hsn_sac_code
        request = self.context.get('request')
        user = request.user if request else None
        tenant = getattr(user, 'active_tenant', user) if user else None
        from billing.gst_utils import validate_hsn_sac_code
        try:
            attrs['hsn_sac_code'] = validate_hsn_sac_code(hsn_sac, tenant)
        except ValueError as e:
            raise serializers.ValidationError({'hsn_sac_code': str(e)})

        cost_price = attrs.get('price')
        if cost_price is None:
            attrs['price'] = self.instance.price if self.instance else 0

        # Validate item_code uniqueness within tenant
        item_code = attrs.get('item_code')
        if item_code:
            item_code = str(item_code).strip()
            attrs['item_code'] = item_code
            request = self.context.get('request')
            user = request.user if request else None
            tenant = getattr(user, 'active_tenant', user) if user else None
            if tenant:
                qs = Product.objects.filter(created_by=tenant, item_code__iexact=item_code)
                if self.instance:
                    qs = qs.exclude(id=self.instance.id)
                if qs.exists():
                    raise serializers.ValidationError({'item_code': f"Item code '{item_code}' is already used by another product."})

        internal_ref = attrs.get('internal_reference')
        if internal_ref:
            attrs['internal_reference'] = str(internal_ref).strip()

        return attrs

    def create(self, validated_data):
        meta_data = validated_data.pop('meta', None)
        # Explicitly set created_by from context if not present (usually validation handles this but good to be safe)
        if 'created_by' not in validated_data:
             validated_data['created_by'] = self.context['request'].user

        product = Product.objects.create(**validated_data)
        
        if meta_data:
            ProductMeta.objects.create(product=product, **meta_data)
        else:
            # Create empty meta to ensure 1-to-1 existence
            ProductMeta.objects.create(product=product)
            
        return product

    def update(self, instance, validated_data):
        meta_data = validated_data.pop('meta', None)
        
        # Update product fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update meta fields
        if meta_data:
            meta, created = ProductMeta.objects.get_or_create(product=instance)
            for attr, value in meta_data.items():
                setattr(meta, attr, value)
            meta.save()
            
        return instance

class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ['id', 'name', 'address', 'gstin', 'is_active', 'created_by']
        read_only_fields = ['id', 'created_by']

class ProductBatchSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = ProductBatch
        fields = [
            'id', 'product', 'product_name', 'batch_number', 'expiry_date', 
            'manufacturing_date', 'mrp', 'cost_price', 'sale_price', 
            'is_active', 'notes'
        ]

class StockPointSerializer(QuantityAsNumberMixin, serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    expiry_date = serializers.DateField(source='batch.expiry_date', read_only=True)
    product_name = serializers.CharField(source='batch.product.name', read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(source='batch.product', read_only=True)
    
    class Meta:
        model = StockPoint
        fields = [
            'id', 'warehouse', 'warehouse_name', 'batch', 'batch_number', 
            'expiry_date', 'quantity', 'product_name', 'product_id'
        ]

class StockTransferItemSerializer(UQCQuantityMixin, serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)

    class Meta:
        model = StockTransferItem
        fields = ['id', 'product', 'product_name', 'batch', 'batch_number', 'quantity', 'rate', 'value']
        read_only_fields = ['rate', 'value']
        extra_kwargs = {'batch': {'required': False}}

class StockTransferSerializer(serializers.ModelSerializer):
    """UAT P1 #15: same-GSTIN transfer = delivery challan at WAC, no GST, capped at stock on hand."""
    items = StockTransferItemSerializer(many=True)
    source_warehouse_name = serializers.CharField(source='source_warehouse.name', read_only=True)
    destination_warehouse_name = serializers.CharField(source='destination_warehouse.name', read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = [
            'id', 'source_warehouse', 'source_warehouse_name', 
            'destination_warehouse', 'destination_warehouse_name',
            'transfer_date', 'status', 'notes', 'items', 'created_by',
            'challan_number', 'total_value', 'warnings',
        ]
        read_only_fields = ['id', 'transfer_date', 'created_by', 'challan_number', 'total_value']

    def get_warnings(self, obj):
        from .transfer_service import transfer_warnings
        return transfer_warnings(obj.total_value or 0)

    def update(self, instance, validated_data):
        # Stock and the journal are posted once at creation; editing lines/locations would desync them.
        validated_data.pop('items', None)
        for f in ('source_warehouse', 'destination_warehouse'):
            validated_data.pop(f, None)
        return super().update(instance, validated_data)

    def create(self, validated_data):
        from django.db import transaction
        from .models import StockPoint
        from .stock_sync import reconcile_product_stock, resolve_batch_for_product
        from .transfer_service import (
            TransferError, next_challan_number, post_transfer_journal, validate_transfer,
        )

        items_data = validated_data.pop('items')
        request = self.context['request']
        tenant = getattr(request.user, 'active_tenant', request.user)
        validated_data['created_by'] = tenant
        # Create as pending first so post_save signal does not move stock before items exist
        target_status = validated_data.get('status') or 'completed'
        validated_data['status'] = 'pending'

        with transaction.atomic():
            lines = []
            for item_data in items_data:
                product = item_data['product']
                batch = item_data.get('batch') or resolve_batch_for_product(product)
                lines.append((product, batch, item_data['quantity']))
            try:
                total_value, valued = validate_transfer(
                    tenant, validated_data.get('source_warehouse'), validated_data.get('destination_warehouse'), lines)
            except TransferError as exc:
                raise serializers.ValidationError({'items': exc.errors})

            validated_data['challan_number'] = next_challan_number(tenant)
            validated_data['total_value'] = total_value
            transfer = StockTransfer.objects.create(**validated_data)

            for v in valued:
                StockTransferItem.objects.create(
                    transfer=transfer, product=v['product'], batch=v['batch'],
                    quantity=v['qty'], rate=v['rate'], value=v['value'],
                )

            if target_status == 'completed':
                for item in transfer.items.select_related('product', 'batch').all():
                    source_stock, _ = StockPoint.objects.select_for_update().get_or_create(
                        warehouse=transfer.source_warehouse,
                        batch=item.batch,
                        defaults={'quantity': 0},
                    )
                    if to_qty(source_stock.quantity or 0) < to_qty(item.quantity):
                        raise serializers.ValidationError({'items': [
                            f"{item.product.name}: only {source_stock.quantity} in stock at "
                            f"{transfer.source_warehouse.name}."]})
                    source_stock.quantity = to_qty(source_stock.quantity or 0) - to_qty(item.quantity)
                    source_stock.save(update_fields=['quantity', 'last_updated'])

                    dest_stock, _ = StockPoint.objects.select_for_update().get_or_create(
                        warehouse=transfer.destination_warehouse,
                        batch=item.batch,
                        defaults={'quantity': 0},
                    )
                    dest_stock.quantity = to_qty(dest_stock.quantity or 0) + to_qty(item.quantity)
                    dest_stock.save(update_fields=['quantity', 'last_updated'])
                    reconcile_product_stock(item.product, save=True)

                transfer._stock_applied_inline = True
                transfer.status = 'completed'
                transfer.save(update_fields=['status'])
                post_transfer_journal(transfer, tenant, entered_by=request.user)

        return transfer


# ── Price Lists & Schemes ──────────────────────────────────────

from .models_pricing import PriceList, PriceListItem, Scheme

class PriceListItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = PriceListItem
        fields = ['id', 'product', 'product_name', 'price', 'min_qty']


class PriceListSerializer(serializers.ModelSerializer):
    items = PriceListItemSerializer(many=True, required=False)

    class Meta:
        model = PriceList
        fields = ['id', 'name', 'currency', 'party_category', 'is_active', 'created_by', 'created_at', 'items']
        read_only_fields = ['id', 'created_by', 'created_at']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        validated_data['created_by'] = self.context['request'].user
        price_list = PriceList.objects.create(**validated_data)
        for item_data in items_data:
            PriceListItem.objects.create(price_list=price_list, **item_data)
        return price_list

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                PriceListItem.objects.create(price_list=instance, **item_data)
        return instance


class SchemeSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    free_product_name = serializers.CharField(source='free_product.name', read_only=True, default=None)

    class Meta:
        model = Scheme
        fields = [
            'id', 'name', 'scheme_type',
            'start_date', 'end_date', 'is_active',
            'product', 'product_name', 'min_qty',
            'free_product', 'free_product_name', 'free_qty',
            'discount_amount', 'discount_percent',
            'created_by',
        ]
        read_only_fields = ['id', 'created_by']

    def create(self, validated_data):
        user = self.context['request'].user
        validated_data['created_by'] = getattr(user, 'active_tenant', user)
        return super().create(validated_data)
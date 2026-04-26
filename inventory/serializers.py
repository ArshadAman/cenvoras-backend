from rest_framework import serializers
from .models import Product, Warehouse, StockPoint, StockTransfer, StockTransferItem, ProductBatch, ProductMeta, BillOfMaterial, StockJournal, StockJournalItem
from .models_sidecar import ProductMeta, BillOfMaterial, StockJournal
from .serializers_sidecar import ProductMetaSerializer, BillOfMaterialSerializer, StockJournalSerializer

class ProductSerializer(serializers.ModelSerializer):
    unit = serializers.ChoiceField(choices=Product.UNIT_CHOICES, required=False)
    cost_price = serializers.DecimalField(source='price', max_digits=10, decimal_places=2, required=False, allow_null=True)
    sale_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=True)
    meta = ProductMetaSerializer(required=False)
    
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'hsn_sac_code', 'description', 'tax', 'stock', 'unit',
            'secondary_unit', 'conversion_factor',
            'cost_price', 'price', 'sale_price', 'warranty_months', 'low_stock_alert', 'created_by',
            'meta'
        ]
        read_only_fields = ['id', 'created_by', 'price']

    def validate(self, attrs):
        sale_price = attrs.get('sale_price')
        if sale_price is None and self.instance is not None:
            sale_price = self.instance.sale_price

        if sale_price in (None, ''):
            raise serializers.ValidationError({'sale_price': 'Sale price is required.'})

        cost_price = attrs.get('price')
        if cost_price is None:
            attrs['price'] = self.instance.price if self.instance else 0

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
        fields = ['id', 'name', 'address', 'is_active', 'created_by']
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

class StockPointSerializer(serializers.ModelSerializer):
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

class StockTransferItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)

    class Meta:
        model = StockTransferItem
        fields = ['id', 'product', 'product_name', 'batch', 'batch_number', 'quantity']

class StockTransferSerializer(serializers.ModelSerializer):
    items = StockTransferItemSerializer(many=True)
    source_warehouse_name = serializers.CharField(source='source_warehouse.name', read_only=True)
    destination_warehouse_name = serializers.CharField(source='destination_warehouse.name', read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = StockTransfer
        fields = [
            'id', 'source_warehouse', 'source_warehouse_name', 
            'destination_warehouse', 'destination_warehouse_name',
            'transfer_date', 'status', 'notes', 'items', 'created_by'
        ]
        read_only_fields = ['id', 'transfer_date', 'created_by']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        transfer = StockTransfer.objects.create(**validated_data)
        
        for item_data in items_data:
            StockTransferItem.objects.create(transfer=transfer, **item_data)
            
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
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
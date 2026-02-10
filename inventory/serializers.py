from rest_framework import serializers
from .models import Product, Warehouse, StockPoint, StockTransfer, StockTransferItem, ProductBatch, ProductMeta, BillOfMaterial, StockJournal, StockJournalItem
from .models_sidecar import ProductMeta, BillOfMaterial, StockJournal
from .serializers_sidecar import ProductMetaSerializer, BillOfMaterialSerializer, StockJournalSerializer

class ProductSerializer(serializers.ModelSerializer):
    meta = ProductMetaSerializer(required=False)
    
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'hsn_sac_code', 'stock', 'unit',
            'secondary_unit', 'conversion_factor',
            'price', 'low_stock_alert', 'created_by',
            'meta'
        ]
        read_only_fields = ['id', 'created_by']

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
    class Meta:
        model = ProductBatch
        fields = ['id', 'product', 'batch_number', 'expiry_date', 'mrp', 'sale_price', 'is_active']

class StockPointSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    expiry_date = serializers.DateField(source='batch.expiry_date', read_only=True)
    
    class Meta:
        model = StockPoint
        fields = ['id', 'warehouse', 'warehouse_name', 'batch', 'batch_number', 'expiry_date', 'quantity']

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
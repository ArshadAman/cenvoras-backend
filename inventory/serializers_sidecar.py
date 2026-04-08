from rest_framework import serializers
from .models import Product
from .models_sidecar import ProductMeta, BillOfMaterial, StockJournal, StockJournalItem

class ProductMetaSerializer(serializers.ModelSerializer):
    secondary_stock = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    bag_weight = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)
    tare_weight = serializers.DecimalField(max_digits=10, decimal_places=3, required=False, allow_null=True)
    mandi_tax = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    labour_charge = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    is_h1 = serializers.BooleanField(required=False, allow_null=True)
    is_narcotic = serializers.BooleanField(required=False, allow_null=True)
    is_new_launch = serializers.BooleanField(required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = ProductMeta
        fields = [
            'barcode', 'expiry_date', 'secondary_stock', 'tags', 'is_h1', 'is_narcotic', 
            'bag_weight', 'tare_weight', 'mandi_tax', 'labour_charge', 
            'is_new_launch', 'salt_composition', 'bundle_items'
        ]

class BillOfMaterialSerializer(serializers.ModelSerializer):
    finished_good = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(), 
        required=False, 
        allow_null=True
    )
    finished_good_display = serializers.SerializerMethodField()
    
    class Meta:
        model = BillOfMaterial
        fields = [
            'id', 'finished_good', 'finished_good_name', 'finished_good_display',
            'name', 'is_active', 'components', 'production_time', 
            'batch_size', 'testing_notes', 'created_by', 'created_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def get_finished_good_display(self, obj):
        if obj.finished_good:
            return obj.finished_good.name
        return obj.finished_good_name

class StockJournalItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    batch_number = serializers.CharField(source='batch.batch_number', read_only=True)
    
    class Meta:
        model = StockJournalItem
        fields = ['id', 'product', 'product_name', 'batch', 'batch_number', 'quantity']

class StockJournalSerializer(serializers.ModelSerializer):
    items = StockJournalItemSerializer(many=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = StockJournal
        fields = [
            'id', 'date', 'voucher_no', 'warehouse', 'warehouse_name',
            'adjustment_type', 'notes', 'items', 'created_by', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'created_by']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        journal = StockJournal.objects.create(**validated_data)
        
        for item_data in items_data:
            StockJournalItem.objects.create(journal=journal, **item_data)
            
        return journal

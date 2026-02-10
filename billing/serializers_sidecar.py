from rest_framework import serializers
from .models_sidecar import TransactionMeta, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, PurchaseIndent, PurchaseIndentItem, InvoiceSettings

class TransactionMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransactionMeta
        fields = ['status', 'delivery_status', 'delivery_boy', 'tags']

class InvoiceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceSettings
        fields = ['print_offset_x', 'print_offset_y', 'template_name', 'terms_conditions', 'header_text', 'footer_text']

class SalesOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = SalesOrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price', 'amount']

class SalesOrderSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = SalesOrder
        fields = ['id', 'order_number', 'date', 'customer', 'customer_name', 'stage', 'total_amount', 'notes', 'items', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_at', 'created_by']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        order = SalesOrder.objects.create(**validated_data)
        
        for item_data in items_data:
            SalesOrderItem.objects.create(order=order, **item_data)
            
        return order

class DeliveryChallanItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = DeliveryChallanItem
        fields = ['id', 'product', 'product_name', 'quantity']

class DeliveryChallanSerializer(serializers.ModelSerializer):
    items = DeliveryChallanItemSerializer(many=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = DeliveryChallan
        fields = ['id', 'challan_number', 'date', 'customer', 'customer_name', 'sales_order', 'is_billed', 'items', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_at', 'created_by', 'is_billed']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        challan = DeliveryChallan.objects.create(**validated_data)
        
        for item_data in items_data:
            DeliveryChallanItem.objects.create(challan=challan, **item_data)
            
        # TODO: Decrease Stock here (implied by Sidecar Pattern)
            
        return challan

class PurchaseIndentItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = PurchaseIndentItem
        fields = ['id', 'product', 'product_name', 'required_quantity']

class PurchaseIndentSerializer(serializers.ModelSerializer):
    items = PurchaseIndentItemSerializer(many=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = PurchaseIndent
        fields = ['id', 'date', 'description', 'status', 'items', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_at', 'created_by']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        validated_data['created_by'] = self.context['request'].user
        
        indent = PurchaseIndent.objects.create(**validated_data)
        
        for item_data in items_data:
            PurchaseIndentItem.objects.create(indent=indent, **item_data)
            
        return indent

from rest_framework import serializers
from .models_sidecar import TransactionMeta, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, PurchaseIndent, PurchaseIndentItem, InvoiceSettings

class TransactionMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransactionMeta
        fields = ['status', 'delivery_status', 'delivery_boy', 'tags']

class PartyMetaSerializer(serializers.ModelSerializer):
    class Meta:
        # Import PartyMeta inside or ensure it's imported at top
        from .models_sidecar import PartyMeta
        model = PartyMeta
        fields = ['loyalty_points', 'party_category', 'credit_days', 'gst_type', 'whatsapp_number']

class InvoiceSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceSettings
        fields = [
            'print_offset_x',
            'print_offset_y',
            'template_name',
            'terms_conditions',
            'header_text',
            'footer_text',
            'show_item_description',
            'show_item_hsn',
            'show_item_batch',
            'require_item_batch',
            'show_item_free_quantity',
            'show_item_discount',
            'show_item_tax',
        ]

class SalesOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = SalesOrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price', 'amount']

class SalesOrderSerializer(serializers.ModelSerializer):
    items = SalesOrderItemSerializer(many=True)
    customer_name = serializers.CharField(write_only=True, required=True)
    customer_display_name = serializers.CharField(source='customer.name', read_only=True)
    customer_email = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    customer_phone = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = SalesOrder
        fields = ['id', 'order_number', 'date', 'customer', 'customer_name', 'customer_display_name', 'customer_email', 'customer_phone', 'stage', 'total_amount', 'notes', 'items', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_at', 'created_by', 'customer']

    def _resolve_customer(self, validated_data):
        """Find or create a Customer from the customer_name field."""
        from .models import Customer
        customer_name = validated_data.pop('customer_name', None)
        customer_email = validated_data.pop('customer_email', None) or ''
        customer_phone = validated_data.pop('customer_phone', None) or ''
        user = self.context['request'].user

        if not customer_name:
            raise serializers.ValidationError({'customer_name': 'Customer name is required.'})

        # Try to find existing customer by name for this user
        customer = Customer.objects.filter(name__iexact=customer_name, created_by=user).first()
        if not customer:
            customer = Customer.objects.create(
                name=customer_name,
                email=customer_email if customer_email else None,
                phone=customer_phone if customer_phone else None,
                created_by=user,
            )
        return customer

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        customer = self._resolve_customer(validated_data)
        validated_data['customer'] = customer
        validated_data['created_by'] = self.context['request'].user
        
        order = SalesOrder.objects.create(**validated_data)
        
        for item_data in items_data:
            SalesOrderItem.objects.create(order=order, **item_data)
            
        return order

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        
        # Handle customer name change
        if 'customer_name' in validated_data:
            customer = self._resolve_customer(validated_data)
            instance.customer = customer
        # Pop leftover write-only fields
        validated_data.pop('customer_email', None)
        validated_data.pop('customer_phone', None)

        # Update scalar fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Replace items if provided
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                SalesOrderItem.objects.create(order=instance, **item_data)

        return instance

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

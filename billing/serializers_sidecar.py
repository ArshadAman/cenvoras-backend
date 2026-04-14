from datetime import date
from decimal import Decimal
from uuid import UUID

from rest_framework import serializers
from .models_sidecar import (
    TransactionMeta,
    SalesOrder,
    SalesOrderItem,
    DeliveryChallan,
    DeliveryChallanItem,
    PurchaseIndent,
    PurchaseIndentItem,
    InvoiceSettings,
    Quotation,
    QuotationItem,
)
from inventory.models import Product

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


class QuotationItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = QuotationItem
        fields = [
            'id',
            'product',
            'product_name',
            'quantity',
            'free_quantity',
            'unit',
            'price',
            'discount',
            'tax',
            'amount',
            'hsn_sac_code',
            'batch',
            'approval_status',
            'converted_to_order',
        ]
        read_only_fields = ['converted_to_order']

    def to_internal_value(self, data):
        mutable = dict(data)
        product_value = mutable.get('product')
        if isinstance(product_value, str):
            # First accept UUID-style product IDs from the shared sales form.
            try:
                UUID(product_value)
                if Product.objects.filter(id=product_value).exists():
                    return super().to_internal_value(mutable)
            except (ValueError, TypeError):
                pass

            # Fallback: allow product names from the existing sales form payload.
            product_obj = Product.objects.filter(name__iexact=product_value).first()
            if not product_obj:
                raise serializers.ValidationError({'product': f'Unknown product: {product_value}'})
            mutable['product'] = str(product_obj.id)
        return super().to_internal_value(mutable)


class QuotationSerializer(serializers.ModelSerializer):
    items = QuotationItemSerializer(many=True, required=False)
    customer_name = serializers.CharField(required=False, allow_blank=True)
    customer_email = serializers.EmailField(write_only=True, required=False, allow_blank=True, allow_null=True)
    customer_phone = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    customer_gstin = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    customer_details = serializers.SerializerMethodField(read_only=True)

    quotation_number = serializers.CharField(required=False, allow_blank=True)
    quotation_date = serializers.DateField(required=False, allow_null=True)

    # Compatibility aliases to keep the existing UI payload working.
    invoice_number = serializers.CharField(write_only=True, required=False, allow_blank=True)
    invoice_date = serializers.DateField(write_only=True, required=False, allow_null=True)
    challan_number = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True)
    challan_date = serializers.DateField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Quotation
        fields = [
            'id',
            'customer',
            'customer_name',
            'customer_details',
            'customer_email',
            'customer_phone',
            'customer_gstin',
            'customer_address',
            'quotation_number',
            'quotation_date',
            'invoice_number',
            'invoice_date',
            'challan_number',
            'challan_date',
            'due_date',
            'po_number',
            'po_date',
            'delivery_address',
            'status',
            'place_of_supply',
            'gst_treatment',
            'journal',
            'warehouse',
            'total_amount',
            'round_off',
            'items',
            'created_by',
            'created_at',
        ]
        read_only_fields = ['created_by', 'created_at']

    def to_internal_value(self, data):
        # Shared forms can send extra invoice-only keys; ignore unknown fields.
        if hasattr(data, 'copy'):
            mutable = data.copy()
            allowed = set(self.fields.keys())
            for key in list(mutable.keys()):
                if key not in allowed:
                    mutable.pop(key, None)
            data = mutable
        return super().to_internal_value(data)

    def get_customer_details(self, obj):
        if not obj.customer:
            return None
        return {
            'id': str(obj.customer.id),
            'name': obj.customer.name,
            'email': obj.customer.email,
            'phone': obj.customer.phone,
            'address': obj.customer.address,
            'gstin': obj.customer.gstin,
            'state': obj.customer.state,
        }

    def validate(self, attrs):
        # Map invoice aliases to quotation fields for compatibility.
        if attrs.get('invoice_number') and not attrs.get('quotation_number'):
            attrs['quotation_number'] = attrs.pop('invoice_number')
        else:
            attrs.pop('invoice_number', None)

        if attrs.get('invoice_date') and not attrs.get('quotation_date'):
            attrs['quotation_date'] = attrs.pop('invoice_date')
        else:
            attrs.pop('invoice_date', None)

        # Ignore sales-invoice-only fields sent by the shared form.
        attrs.pop('challan_number', None)
        attrs.pop('challan_date', None)

        # Provide safe defaults so shared form edge-cases do not hard-fail create.
        if self.instance is None:
            if not attrs.get('quotation_number'):
                attrs['quotation_number'] = f"QT-{date.today().strftime('%Y%m%d')}-AUTO"
            if not attrs.get('quotation_date'):
                attrs['quotation_date'] = date.today()

        return attrs

    def _resolve_customer(self, validated_data):
        from .models import Customer

        user = self.context['request'].user.active_tenant
        customer_name = validated_data.get('customer_name')
        customer_email = validated_data.pop('customer_email', None)
        customer_phone = validated_data.pop('customer_phone', None)
        customer_gstin = validated_data.pop('customer_gstin', None)

        if not customer_name:
            return None

        customer = Customer.objects.filter(name__iexact=customer_name, created_by=user).first()
        if customer:
            updated = False
            if customer_email and not customer.email:
                customer.email = customer_email
                updated = True
            if customer_phone and not customer.phone:
                customer.phone = customer_phone
                updated = True
            if validated_data.get('customer_address') and not customer.address:
                customer.address = validated_data.get('customer_address')
                updated = True
            if customer_gstin and not customer.gstin:
                customer.gstin = customer_gstin
                updated = True
            if updated:
                customer.save()
            return customer

        return Customer.objects.create(
            name=customer_name,
            email=customer_email or None,
            phone=customer_phone or None,
            address=validated_data.get('customer_address') or None,
            gstin=customer_gstin or None,
            created_by=user,
        )

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        customer = self._resolve_customer(validated_data)
        validated_data['customer'] = customer
        validated_data['created_by'] = self.context['request'].user.active_tenant

        quotation = Quotation.objects.create(**validated_data)
        for item_data in items_data:
            QuotationItem.objects.create(quotation=quotation, **item_data)

        if items_data:
            total = sum(Decimal(str(item.amount)) for item in quotation.items.all())
            quotation.total_amount = total + Decimal(str(quotation.round_off or 0))
            quotation.save(update_fields=['total_amount'])

        return quotation

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        customer = self._resolve_customer(validated_data)
        if customer:
            instance.customer = customer

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                QuotationItem.objects.create(quotation=instance, **item_data)

            total = sum(Decimal(str(item.amount)) for item in instance.items.all())
            instance.total_amount = total + Decimal(str(instance.round_off or 0))
            instance.save(update_fields=['total_amount'])

        return instance

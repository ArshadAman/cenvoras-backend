from rest_framework import serializers
from .models import PurchaseOrder, PurchaseOrderItem, Vendor
from inventory.serializers import ProductSerializer


class VendorSimpleSerializer(serializers.ModelSerializer):
    """Simple vendor serializer for nested representation."""
    class Meta:
        model = Vendor
        fields = ['id', 'name', 'email', 'phone', 'gstin']


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    product_display_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'product', 'product_name', 'product_display_name', 'batch', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Product
        from rest_framework.relations import PrimaryKeyRelatedField
        self.fields['product'] = PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)

    def to_internal_value(self, data):
        mutable = dict(data)
        product_value = mutable.get('product')
        product_name = mutable.get('product_name')
        
        request = self.context.get('request')
        tenant = getattr(request.user, 'active_tenant', request.user) if request and hasattr(request, 'user') else None

        if not product_value and product_name and tenant:
            from inventory.models import Product
            # Create product on the fly
            product_obj = Product.objects.filter(name__iexact=product_name, created_by=tenant).first()
            if not product_obj:
                product_obj = Product.objects.create(
                    name=product_name,
                    unit=mutable.get('unit') or 'pcs',
                    price=mutable.get('price') or 0,
                    created_by=tenant,
                )
            mutable['product'] = str(product_obj.id)
        
        ret = super().to_internal_value(mutable)
        if not ret.get('product'):
            raise serializers.ValidationError({'product': 'Product is required and could not be resolved.'})
        return ret


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, required=False)
    vendor = VendorSimpleSerializer(read_only=True)
    vendor_id = serializers.PrimaryKeyRelatedField(
        queryset=Vendor.objects.all(), source='vendor', write_only=True, required=False, allow_null=True
    )
    vendor_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    vendor_display_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'po_number', 'vendor', 'vendor_id', 'vendor_name', 'vendor_display_name', 'expected_date', 'status', 'notes', 'total_amount', 'items', 'created_at']

    def get_vendor_display_name(self, obj):
        """Return vendor name or empty string if vendor is null."""
        return obj.vendor.name if obj.vendor else 'Unspecified Vendor'

    def _resolve_vendor(self, validated_data):
        from .models import Vendor
        request = self.context.get('request')
        tenant = getattr(request.user, 'active_tenant', request.user) if request and hasattr(request, 'user') else None
        
        vendor_id = validated_data.get('vendor')
        vendor_name = validated_data.pop('vendor_name', None)
        
        if vendor_id:
            return vendor_id
            
        if vendor_name and tenant:
            vendor = Vendor.objects.filter(name__iexact=vendor_name, created_by=tenant).first()
            if not vendor:
                vendor = Vendor.objects.create(name=vendor_name, created_by=tenant)
            return vendor
        return None

    def create(self, validated_data):
        items = validated_data.pop('items', [])
        vendor = self._resolve_vendor(validated_data)
        validated_data['vendor'] = vendor
        po = PurchaseOrder.objects.create(**validated_data)
        for item in items:
            item.pop('product_name', None)
            PurchaseOrderItem.objects.create(purchase_order=po, **item)
        return po

    def update(self, instance, validated_data):
        items = validated_data.pop('items', None)
        vendor = self._resolve_vendor(validated_data)
        if vendor:
            validated_data['vendor'] = vendor
            
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if items is not None:
            instance.items.all().delete()
            for item in items:
                item.pop('product_name', None)
                PurchaseOrderItem.objects.create(purchase_order=instance, **item)
        return instance

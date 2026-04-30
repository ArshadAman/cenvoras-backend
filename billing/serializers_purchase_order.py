from rest_framework import serializers
from .models import PurchaseOrder, PurchaseOrderItem, Vendor
from inventory.serializers import ProductSerializer


class VendorSimpleSerializer(serializers.ModelSerializer):
    """Simple vendor serializer for nested representation."""
    class Meta:
        model = Vendor
        fields = ['id', 'name', 'email', 'phone', 'gstin']


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    # instantiate as read_only to avoid DRF assertion at import time;
    # replace with a proper field in __init__ when apps are ready
    product = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'product', 'batch', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Product
        from rest_framework.relations import PrimaryKeyRelatedField
        self.fields['product'] = PrimaryKeyRelatedField(queryset=Product.objects.all())


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, required=False)
    vendor = VendorSimpleSerializer(read_only=True)
    vendor_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'po_number', 'vendor', 'vendor_name', 'expected_date', 'status', 'notes', 'total_amount', 'items', 'created_at']

    def get_vendor_name(self, obj):
        """Return vendor name or empty string if vendor is null."""
        return obj.vendor.name if obj.vendor else 'Unspecified Vendor'

    def create(self, validated_data):
        items = validated_data.pop('items', [])
        po = PurchaseOrder.objects.create(**validated_data)
        for item in items:
            PurchaseOrderItem.objects.create(purchase_order=po, **item)
        return po

    def update(self, instance, validated_data):
        items = validated_data.pop('items', None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if items is not None:
            instance.items.all().delete()
            for item in items:
                PurchaseOrderItem.objects.create(purchase_order=instance, **item)
        return instance

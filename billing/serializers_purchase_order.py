from rest_framework import serializers
from .models import PurchaseOrder, PurchaseOrderItem
from inventory.serializers import ProductSerializer


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=None)

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'product', 'batch', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Product
        self.fields['product'].queryset = Product.objects.all()


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True)

    class Meta:
        model = PurchaseOrder
        fields = ['id', 'po_number', 'vendor', 'expected_date', 'status', 'notes', 'total_amount', 'items']

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

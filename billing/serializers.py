from rest_framework import serializers
from .models import PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem
from inventory.models import Product
import uuid

class ProductField(serializers.Field):
    def to_internal_value(self, value):
        if not value or not str(value).strip():
            raise serializers.ValidationError('Product is required.')
        return value

    def to_representation(self, value):
        return str(value)

class PurchaseBillItemSerializer(serializers.ModelSerializer):
    product = ProductField()
    hsn_sac_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    price = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)

    class Meta:
        model = PurchaseBillItem
        fields = [
            'product', 'hsn_sac_code', 'unit',
            'quantity', 'price', 'amount', 'discount', 'tax'
        ]

    def to_internal_value(self, data):
        print("DEBUG product value:", data.get('product'), type(data.get('product')))
        product_value = data.get('product')
        if not product_value or not str(product_value).strip():
            raise serializers.ValidationError({'product': 'Product is required.'})
        user = self.context['request'].user

        try:
            uuid_obj = uuid.UUID(str(product_value))
            product = Product.objects.get(id=uuid_obj)
            # Update product fields if present in data
            updated = False
            for field in ['hsn_sac_code', 'unit', 'price', 'tax']:
                if field in data and data[field] is not None:
                    setattr(product, field, data[field])
                    updated = True
            if updated:
                product.save()
        except (ValueError, Product.DoesNotExist):
            # Not a UUID or not found, create new product
            defaults = {
                'hsn_sac_code': data.get('hsn_sac_code', ''),
                'unit': data.get('unit', 'pcs'),
                'price': data.get('price', 0),
                'tax': data.get('tax', 0),
                'created_by': user,
            }
            product, created = Product.objects.get_or_create(
                name=product_value,
                defaults=defaults
            )
        data['product'] = product
        return super().to_internal_value(data)

class PurchaseBillSerializer(serializers.ModelSerializer):
    items = PurchaseBillItemSerializer(many=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = PurchaseBill
        fields = [
            'id', 'bill_number', 'bill_date', 'due_date', 'vendor_name', 'vendor_address', 'vendor_gstin',
            'gst_treatment', 'journal', 'total_amount', 'created_by', 'created_at', 'items'
        ]

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        purchase_bill = PurchaseBill.objects.create(**validated_data)
        for item_data in items_data:
            PurchaseBillItem.objects.create(purchase_bill=purchase_bill, **item_data)
        return purchase_bill

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        
        # Update the purchase bill fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Delete existing items and create new ones
        instance.items.all().delete()
        for item_data in items_data:
            PurchaseBillItem.objects.create(purchase_bill=instance, **item_data)
        
        return instance

class SalesInvoiceItemSerializer(serializers.ModelSerializer):
    product_detail = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SalesInvoiceItem
        fields = ['id', 'product', 'product_detail', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def get_product_detail(self, obj):
        return {
            "name": obj.product.name,
            "hsn_sac_code": obj.product.hsn_sac_code,
            "unit": obj.product.unit,
        }

class SalesInvoiceSerializer(serializers.ModelSerializer):
    items = SalesInvoiceItemSerializer(many=True)

    class Meta:
        model = SalesInvoice
        fields = ['id', 'customer', 'invoice_number', 'invoice_date', 'due_date', 'delivery_address', 'gst_treatment',
                  'journal', 'total_amount', 'created_by', 'created_at', 'items']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        sales_invoice = SalesInvoice.objects.create(**validated_data)
        for item_data in items_data:
            SalesInvoiceItem.objects.create(sales_invoice=sales_invoice, **item_data)
        return sales_invoice
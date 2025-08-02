from rest_framework import serializers
from .models import PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem
from inventory.models import Product

class PurchaseBillItemSerializer(serializers.ModelSerializer):
    product_detail = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PurchaseBillItem
        fields = ['id', 'product', 'product_detail', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def get_product_detail(self, obj):
        return {
            "name": obj.product.name,
            "hsn_code": obj.product.hsn_code,
            "unit": obj.product.unit,
        }

class PurchaseBillSerializer(serializers.ModelSerializer):
    items = PurchaseBillItemSerializer(many=True)

    class Meta:
        model = PurchaseBill
        fields = ['id', 'bill_number', 'bill_date', 'due_date', 'vendor_name', 'vendor_address', 'vendor_gstin',
                  'gst_treatment', 'journal', 'total_amount', 'created_by', 'created_at', 'items']

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        purchase_bill = PurchaseBill.objects.create(**validated_data)
        for item_data in items_data:
            PurchaseBillItem.objects.create(purchase_bill=purchase_bill, **item_data)
        return purchase_bill

class SalesInvoiceItemSerializer(serializers.ModelSerializer):
    product_detail = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SalesInvoiceItem
        fields = ['id', 'product', 'product_detail', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def get_product_detail(self, obj):
        return {
            "name": obj.product.name,
            "hsn_code": obj.product.hsn_code,
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
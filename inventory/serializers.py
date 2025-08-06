from rest_framework import serializers
from .models import Product

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'hsn_sac_code', 'stock', 'unit',
            'price', 'low_stock_alert', 'created_by'
        ]
        read_only_fields = ['id', 'created_by']
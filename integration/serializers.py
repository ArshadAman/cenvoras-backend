from rest_framework import serializers
from .models import ApiKey, NotificationLog, NotificationTemplate


class ApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = '__all__'
        read_only_fields = ('key', 'user', 'last_used_at')


class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationLog
        fields = '__all__'
        read_only_fields = ('user',)


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = '__all__'
        read_only_fields = ('user',)


class SendInvoiceNotificationSerializer(serializers.Serializer):
    invoice_id = serializers.UUIDField()
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=['email', 'whatsapp']),
        default=['email']
    )


class BarcodeProductSerializer(serializers.Serializer):
    """Response serializer for barcode lookup"""
    id = serializers.UUIDField()
    name = serializers.CharField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    sale_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    stock = serializers.IntegerField()
    hsn_sac_code = serializers.CharField()
    barcode = serializers.CharField()

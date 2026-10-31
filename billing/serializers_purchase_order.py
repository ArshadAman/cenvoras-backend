from django.db import IntegrityError
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import PurchaseOrder, PurchaseOrderItem, Vendor
from inventory.serializers import ProductSerializer


def _raise_number_conflict(exc):
    error = APIException({'error': str(exc), 'details': str(exc)})
    error.status_code = 409
    raise error


class VendorSimpleSerializer(serializers.ModelSerializer):
    """Simple vendor serializer for nested representation."""
    class Meta:
        model = Vendor
        fields = ['id', 'name', 'email', 'phone', 'gstin']


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    product_display_name = serializers.CharField(source='product.name', read_only=True)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    source_item_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ['id', 'product', 'product_name', 'product_display_name', 'description', 'batch', 'quantity', 'unit', 'price', 'discount', 'tax', 'amount', 'source_item_id']

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['product_description'] = instance.description or (instance.product.description if instance.product else '') or ''
        return ret

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from inventory.models import Product
        from rest_framework.relations import PrimaryKeyRelatedField
        self.fields['product'] = PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)

    def to_internal_value(self, data):
        mutable = dict(data)
        if 'product_description' in mutable and not mutable.get('description'):
            mutable['description'] = mutable.get('product_description') or ''
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
        extra_kwargs = {'po_number': {'required': False, 'allow_blank': True, 'allow_null': True}}

    def get_vendor_display_name(self, obj):
        """Return vendor name or empty string if vendor is null."""
        return obj.vendor.name if obj.vendor else 'Unspecified Vendor'

    def validate(self, attrs):
        vendor = attrs.get('vendor')
        vendor_name = (attrs.get('vendor_name') or '').strip()
        if vendor:
            return attrs
        request = self.context.get('request')
        tenant = getattr(request.user, 'active_tenant', request.user) if request and hasattr(request, 'user') else None
        if vendor_name and tenant:
            from .models import Vendor
            if Vendor.objects.filter(name__iexact=vendor_name, created_by=tenant).exists():
                return attrs
        if self.instance and getattr(self.instance, 'vendor_id', None) and 'vendor_name' not in self.initial_data:
            return attrs
        raise serializers.ValidationError({
            'vendor_name': 'Save this vendor first before creating a bill or purchase order.'
        })

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
                raise serializers.ValidationError({
                    'vendor_name': 'Save this vendor first before creating a bill or purchase order.'
                })
            return vendor
        return None

    def create(self, validated_data):
        items = validated_data.pop('items', [])
        vendor = self._resolve_vendor(validated_data)
        validated_data['vendor'] = vendor
        request = self.context.get('request')
        tenant = getattr(getattr(request, 'user', None), 'active_tenant', getattr(request, 'user', None)) if request else None
        if tenant:
            from billing.sequence_service import assign_document_number, DuplicateDocumentNumber, document_status_from
            try:
                validated_data['po_number'] = assign_document_number(
                    tenant,
                    'purchase_order',
                    validated_data.get('po_number'),
                    status=document_status_from(validated_data, self, default='draft'),
                    prefix='PO-',
                    on_collision='conflict',
                ) or None
            except DuplicateDocumentNumber as exc:
                _raise_number_conflict(exc)
        try:
            po = PurchaseOrder.objects.create(**validated_data)
        except IntegrityError:
            from billing.sequence_service import DuplicateDocumentNumber
            _raise_number_conflict(DuplicateDocumentNumber(validated_data.get('po_number'), 'purchase_order'))
        for item in items:
            item.pop('product_name', None)
            PurchaseOrderItem.objects.create(purchase_order=po, **item)
        return po

    def update(self, instance, validated_data):
        items = validated_data.pop('items', None)
        vendor = self._resolve_vendor(validated_data)
        if vendor:
            validated_data['vendor'] = vendor

        old_status = instance.status
        new_status = validated_data.get('status', old_status)
        from billing.sequence_service import assign_document_number, DuplicateDocumentNumber
        candidate = validated_data.get('po_number', instance.po_number)
        try:
            assigned = assign_document_number(
                instance.created_by,
                'purchase_order',
                candidate,
                status=new_status,
                exclude_pk=instance.pk,
                prefix='PO-',
                on_collision='allocate',
            )
            validated_data['po_number'] = assigned or None
        except DuplicateDocumentNumber as exc:
            _raise_number_conflict(exc)
            
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if items is not None:
            for item in items:
                item.pop('product_name', None)
            from billing.sync_service import DocumentSyncService
            DocumentSyncService.sync_document_items(instance, items, user=instance.created_by)
        return instance

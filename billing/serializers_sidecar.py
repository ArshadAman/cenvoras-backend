from datetime import date
from decimal import Decimal
from uuid import UUID

from rest_framework import serializers
from subscription.services import can_auto_create_inventory_product
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
from inventory.models import Product, Warehouse, ProductBatch, StockPoint
from billing.models import Customer
from django.db.models import F

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
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    discount = serializers.DecimalField(required=False, default=0, max_digits=8, decimal_places=2)
    tax = serializers.DecimalField(required=False, default=0, max_digits=8, decimal_places=2)
    free_quantity = serializers.IntegerField(min_value=0, required=False, default=0)
    
    class Meta:
        model = SalesOrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'free_quantity', 'unit', 'price', 'discount', 'tax', 'amount']

    def to_internal_value(self, data):
        # Allow passing product name instead of UUID
        product_value = data.get('product')
        if not product_value:
            raise serializers.ValidationError({'product': 'Product is required.'})

        user = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        product_obj = None

        try:
            # Try UUID first
            product_uuid = UUID(str(product_value))
            product_obj = Product.objects.filter(id=product_uuid, created_by=user).first()
        except (ValueError, TypeError):
            # Try name
            product_obj = Product.objects.filter(name__iexact=str(product_value).strip(), created_by=user).first()
            
            if not product_obj:
                # If product doesn't exist, we might want to create it if plan allows, 
                # but for Sales Order we'll just fail if it's not found for now 
                # to keep it consistent with other non-accounting vouchers unless specified.
                # Actually, let's auto-create it if possible to match Sales Invoice behavior.
                can_auto_create = can_auto_create_inventory_product(user)
                if can_auto_create:
                    product_obj = Product.objects.create(
                        name=str(product_value).strip(),
                        price=data.get('price', 0),
                        unit=data.get('unit') or 'pcs',
                        created_by=user
                    )
                else:
                    raise serializers.ValidationError({'product': f'Product "{product_value}" not found in inventory.'})

        data['product'] = product_obj.id
        if not data.get('unit') and product_obj and product_obj.unit:
            data['unit'] = product_obj.unit
        return super().to_internal_value(data)

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
        user = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)

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
        validated_data['created_by'] = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        
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
    product_detail = serializers.SerializerMethodField(read_only=True)
    batch = serializers.PrimaryKeyRelatedField(queryset=ProductBatch.objects.all(), required=False, allow_null=True)

    class Meta:
        model = DeliveryChallanItem
        fields = [
            'id',
            'product',
            'product_name',
            'product_detail',
            'quantity',
            'free_quantity',
            'unit',
            'price',
            'discount',
            'tax',
            'amount',
            'hsn_sac_code',
            'batch',
        ]

    def get_product_detail(self, obj):
        if not obj.product:
            return None
        return {
            "id": str(obj.product.id),
            "name": obj.product.name,
            "description": getattr(obj.product, 'description', '') or '',
            "hsn_sac_code": getattr(obj.product, 'hsn_sac_code', '') or '',
            "unit": getattr(obj.product, 'unit', 'pcs') or 'pcs',
        }

    def _get_tenant(self):
        request = self.context.get('request')
        if not request or not hasattr(request, 'user'):
            raise serializers.ValidationError({'product': 'Authentication required.'})
        return getattr(request.user, 'active_tenant', request.user)

    def to_internal_value(self, data):
        mutable = dict(data)
        product_value = mutable.get('product')
        if not product_value or not str(product_value).strip():
            raise serializers.ValidationError({'product': 'Product is required.'})

        tenant = self._get_tenant()
        can_auto_create = can_auto_create_inventory_product(tenant)
        product_obj = None

        if isinstance(product_value, str):
            product_value = product_value.strip()

        try:
            product_uuid = UUID(str(product_value))
            product_obj = Product.objects.filter(id=product_uuid, created_by=tenant).first()
            if not product_obj:
                raise serializers.ValidationError({'product': f'Product with ID {product_uuid} does not exist.'})
        except (ValueError, TypeError):
            product_name = str(product_value).strip()
            product_obj = Product.objects.filter(name__iexact=product_name, created_by=tenant).first()

            if product_obj:
                updated_fields = []
                for field in ['hsn_sac_code', 'unit', 'price', 'tax']:
                    field_value = mutable.get(field)
                    if field_value is None:
                        continue
                    if isinstance(field_value, str) and not field_value.strip():
                        continue
                    setattr(product_obj, field, field_value)
                    updated_fields.append(field)
                if updated_fields:
                    product_obj.save(update_fields=updated_fields)
            else:
                if not can_auto_create:
                    raise serializers.ValidationError({
                        'product': 'Only Pro and above plans can create new inventory products.'
                    })
                product_obj = Product.objects.create(
                    name=product_name,
                    hsn_sac_code=mutable.get('hsn_sac_code') or '',
                    unit=mutable.get('unit') or 'pcs',
                    price=mutable.get('price') or 0,
                    tax=mutable.get('tax') or 0,
                    created_by=tenant,
                )

        mutable['product'] = str(product_obj.id)
        return super().to_internal_value(mutable)


class DeliveryChallanSerializer(serializers.ModelSerializer):
    items = DeliveryChallanItemSerializer(many=True, required=False)
    customer_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    customer_address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    customer_gstin = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    customer_details = serializers.SerializerMethodField(read_only=True)
    sales_order_number = serializers.CharField(source='sales_order.order_number', read_only=True, allow_null=True)
    sales_order_details = serializers.SerializerMethodField(read_only=True)
    challan_number = serializers.CharField(required=False, allow_blank=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = DeliveryChallan
        fields = [
            'id',
            'challan_number',
            'date',
            'customer',
            'customer_name',
            'customer_address',
            'customer_gstin',
            'customer_details',
            'delivery_address',
            'vehicle_number',
            'transport_mode',
            'eway_bill_number',
            'po_number',
            'po_date',
            'sales_order',
            'sales_order_number',
            'sales_order_details',
            'warehouse',
            'total_amount',
            'round_off',
            'status',
            'is_billed',
            'converted_invoice',
            'notes',
            'items',
            'created_by',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'created_by', 'is_billed', 'converted_invoice']

    def get_customer_details(self, obj):
        if obj.customer:
            return {
                "id": str(obj.customer.id),
                "name": obj.customer.name,
                "address": obj.customer.address or "",
                "phone": getattr(obj.customer, 'phone', '') or '',
                "email": getattr(obj.customer, 'email', '') or '',
                "gstin": getattr(obj.customer, 'gstin', '') or '',
                "state": getattr(obj.customer, 'state', '') or '',
            }
        return {
            "name": obj.customer_name or "",
            "address": obj.customer_address or "",
            "gstin": obj.customer_gstin or "",
        }

    def get_sales_order_details(self, obj):
        if obj.sales_order:
            return {
                "id": str(obj.sales_order.id),
                "order_number": obj.sales_order.order_number,
                "date": str(obj.sales_order.date),
                "stage": obj.sales_order.stage,
                "total_amount": str(obj.sales_order.total_amount),
            }
        return None

    @staticmethod
    def _calculate_line_amount(item_data):
        quantity = Decimal(str(item_data.get('quantity', 0) or 0))
        price = Decimal(str(item_data.get('price', 0) or 0))
        discount = Decimal(str(item_data.get('discount', 0) or 0))
        tax = Decimal(str(item_data.get('tax', 0) or 0))

        base_amount = quantity * price
        discount_amount = (base_amount * discount) / Decimal('100')
        taxable_amount = base_amount - discount_amount
        tax_amount = (taxable_amount * tax) / Decimal('100')
        return (taxable_amount + tax_amount).quantize(Decimal('0.01'))

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        user = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        validated_data['created_by'] = user

        # Auto-allocate challan number if empty
        if not validated_data.get('challan_number'):
            from billing.sequence_service import allocate_next_number
            raw_prefix = self.context['request'].data.get('prefix', 'DC-') if 'request' in self.context else 'DC-'
            validated_data['challan_number'] = allocate_next_number(
                tenant=user,
                document_type='delivery_challan',
                prefix=raw_prefix
            )

        # Smart customer resolution
        cust_name = validated_data.get('customer_name')
        if not validated_data.get('customer') and cust_name:
            existing_cust = Customer.objects.filter(name__iexact=cust_name.strip(), created_by=user).first()
            if existing_cust:
                validated_data['customer'] = existing_cust
            else:
                validated_data['customer'] = Customer.objects.create(
                    name=cust_name.strip(),
                    address=validated_data.get('customer_address') or '',
                    gstin=validated_data.get('customer_gstin') or '',
                    created_by=user,
                )

        # Calculate line amounts and total
        total = Decimal('0.00')
        processed_items = []
        for item_data in items_data:
            line_amt = self._calculate_line_amount(item_data)
            item_data['amount'] = line_amt
            total += line_amt
            processed_items.append(item_data)

        round_off = validated_data.get('round_off', Decimal('0.00')) or Decimal('0.00')
        validated_data['total_amount'] = total + round_off

        challan = DeliveryChallan.objects.create(**validated_data)

        for item_data in processed_items:
            DeliveryChallanItem.objects.create(challan=challan, **item_data)

        # Deduct stock for dispatched goods
        target_warehouse = challan.warehouse
        for item in challan.items.all():
            eff_qty = (item.quantity or 0) + (item.free_quantity or 0)
            if eff_qty > 0:
                Product.objects.filter(pk=item.product_id).update(stock=F('stock') - eff_qty)
                if item.batch and target_warehouse:
                    sp, _ = StockPoint.objects.get_or_create(
                        batch=item.batch, warehouse=target_warehouse, defaults={'quantity': 0}
                    )
                    StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - eff_qty)

        return challan

    def update(self, instance, validated_data):
        if instance.is_billed:
            raise serializers.ValidationError({"detail": "Cannot modify an invoiced delivery challan."})

        items_data = validated_data.pop('items', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if items_data is not None:
            # Restore stock for existing items
            old_warehouse = instance.warehouse
            for old_item in instance.items.all():
                old_qty = (old_item.quantity or 0) + (old_item.free_quantity or 0)
                if old_qty > 0:
                    Product.objects.filter(pk=old_item.product_id).update(stock=F('stock') + old_qty)
                    if old_item.batch and old_warehouse:
                        StockPoint.objects.filter(batch=old_item.batch, warehouse=old_warehouse).update(
                            quantity=F('quantity') + old_qty
                        )

            instance.items.all().delete()

            total = Decimal('0.00')
            for item_data in items_data:
                item_data['amount'] = self._calculate_line_amount(item_data)
                total += item_data['amount']
                DeliveryChallanItem.objects.create(challan=instance, **item_data)

            round_off = instance.round_off or Decimal('0.00')
            instance.total_amount = total + round_off

            # Deduct stock for new items
            new_warehouse = instance.warehouse
            for new_item in instance.items.all():
                eff_qty = (new_item.quantity or 0) + (new_item.free_quantity or 0)
                if eff_qty > 0:
                    Product.objects.filter(pk=new_item.product_id).update(stock=F('stock') - eff_qty)
                    if new_item.batch and new_warehouse:
                        sp, _ = StockPoint.objects.get_or_create(
                            batch=new_item.batch, warehouse=new_warehouse, defaults={'quantity': 0}
                        )
                        StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - eff_qty)

        instance.save()
        return instance

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
        validated_data['created_by'] = getattr(self.context['request'].user, 'active_tenant', self.context['request'].user)
        
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

    def _get_tenant(self):
        request = self.context.get('request')
        if not request or not hasattr(request, 'user'):
            raise serializers.ValidationError({'product': 'Authentication required.'})
        return getattr(request.user, 'active_tenant', request.user)

    def to_internal_value(self, data):
        mutable = dict(data)
        product_value = mutable.get('product')
        if not product_value or not str(product_value).strip():
            raise serializers.ValidationError({'product': 'Product is required.'})

        tenant = self._get_tenant()
        can_auto_create_inventory = can_auto_create_inventory_product(tenant)
        product_obj = None

        if isinstance(product_value, str):
            product_value = product_value.strip()

        try:
            product_uuid = UUID(str(product_value))
            product_obj = Product.objects.filter(id=product_uuid, created_by=tenant).first()
            if not product_obj:
                raise serializers.ValidationError({'product': f'Product with ID {product_uuid} does not exist.'})
        except (ValueError, TypeError):
            product_name = str(product_value).strip()
            product_obj = Product.objects.filter(name__iexact=product_name, created_by=tenant).first()

            if product_obj:
                updated_fields = []
                for field in ['hsn_sac_code', 'unit', 'price', 'tax']:
                    field_value = mutable.get(field)
                    if field_value is None:
                        continue
                    if isinstance(field_value, str) and not field_value.strip():
                        continue
                    setattr(product_obj, field, field_value)
                    updated_fields.append(field)
                if updated_fields:
                    product_obj.save(update_fields=updated_fields)
            else:
                if not can_auto_create_inventory:
                    raise serializers.ValidationError({
                        'product': 'Only Pro and above plans can create new inventory products from quotation forms.'
                    })
                product_obj = Product.objects.create(
                    name=product_name,
                    hsn_sac_code=mutable.get('hsn_sac_code') or '',
                    unit=mutable.get('unit') or 'pcs',
                    price=mutable.get('price') or 0,
                    tax=mutable.get('tax') or 0,
                    created_by=tenant,
                )

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

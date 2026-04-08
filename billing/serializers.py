from rest_framework import serializers
from rest_framework import serializers
from .models import PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Customer, Vendor, Payment
from .models_sidecar import TransactionMeta, SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, PurchaseIndent, PurchaseIndentItem, InvoiceSettings
from .serializers_sidecar import TransactionMetaSerializer, SalesOrderSerializer, DeliveryChallanSerializer, PurchaseIndentSerializer, InvoiceSettingsSerializer
from inventory.models import Product, ProductBatch
from cenvoras.constants import IndianStates
import uuid
from decimal import Decimal


GST_STATE_CODE_TO_ALPHA = {
    '01': IndianStates.JAMMU_AND_KASHMIR,
    '02': IndianStates.HIMACHAL_PRADESH,
    '03': IndianStates.PUNJAB,
    '04': IndianStates.CHANDIGARH,
    '05': IndianStates.UTTARAKHAND,
    '06': IndianStates.HARYANA,
    '07': IndianStates.DELHI,
    '08': IndianStates.RAJASTHAN,
    '09': IndianStates.UTTAR_PRADESH,
    '10': IndianStates.BIHAR,
    '11': IndianStates.SIKKIM,
    '12': IndianStates.ARUNACHAL_PRADESH,
    '13': IndianStates.NAGALAND,
    '14': IndianStates.MANIPUR,
    '15': IndianStates.MIZORAM,
    '16': IndianStates.TRIPURA,
    '17': IndianStates.MEGHALAYA,
    '18': IndianStates.ASSAM,
    '19': IndianStates.WEST_BENGAL,
    '20': IndianStates.JHARKHAND,
    '21': IndianStates.ODISHA,
    '22': IndianStates.CHHATTISGARH,
    '23': IndianStates.MADHYA_PRADESH,
    '24': IndianStates.GUJARAT,
    '26': IndianStates.DADRA_AND_NAGAR_HAVELI_AND_DAMAN_AND_DIU,
    '27': IndianStates.MAHARASHTRA,
    '29': IndianStates.KARNATAKA,
    '30': IndianStates.GOA,
    '31': IndianStates.LAKSHADWEEP,
    '32': IndianStates.KERALA,
    '33': IndianStates.TAMIL_NADU,
    '34': IndianStates.PUDUCHERRY,
    '35': IndianStates.ANDAMAN_AND_NICOBAR_ISLANDS,
    '36': IndianStates.TELANGANA,
    '37': IndianStates.ANDHRA_PRADESH,
    '38': IndianStates.LADAKH,
}


def normalize_indian_state_choice(value):
    if value is None:
        return value
    raw = str(value).strip()
    if not raw:
        return None

    # Support GST numeric codes (e.g. 29 -> KA) and decorated values (e.g. 29-Karnataka)
    numeric = raw.split('-', 1)[0].strip()
    if numeric.isdigit():
        numeric = numeric.zfill(2)
        mapped = GST_STATE_CODE_TO_ALPHA.get(numeric)
        if mapped:
            return mapped

    upper = raw.upper()
    valid_choices = {choice for choice, _label in IndianStates.choices}
    if upper in valid_choices:
        return upper

    return raw

class ProductField(serializers.Field):
    def to_internal_value(self, value):
        if not value or not str(value).strip():
            raise serializers.ValidationError('Product is required.')
        return value

    def to_representation(self, value):
        return str(value)

class CustomerField(serializers.Field):
    def to_internal_value(self, value):
        if not value:
            raise serializers.ValidationError('Customer is required.')
        return value

    def to_representation(self, value):
        return str(value)

class PurchaseBillItemSerializer(serializers.ModelSerializer):
    product = ProductField()
    hsn_sac_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    price = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)
    
    # Batch fields (Virtual fields, not mapped directly to model until to_internal_value)
    batch_number = serializers.CharField(required=False, write_only=True)
    expiry_date = serializers.DateField(required=False, write_only=True, allow_null=True)
    mrp = serializers.DecimalField(required=False, write_only=True, max_digits=10, decimal_places=2)

    class Meta:
        model = PurchaseBillItem
        fields = [
            'product', 'hsn_sac_code', 'unit',
            'quantity', 'price', 'amount', 'discount', 'tax',
            'batch_number', 'expiry_date', 'mrp'
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
        
        # Handle Batch Logic
        batch_number = data.get('batch_number')
        if batch_number:
            print(f"DEBUG: Processing batch {batch_number} for product {product.name}")
            expiry_date = data.get('expiry_date')
            mrp = data.get('mrp', 0)
            cost_price = data.get('price', 0) # Cost is the purchase price
            
            batch, created = ProductBatch.objects.get_or_create(
                product=product,
                batch_number=batch_number,
                defaults={
                    'expiry_date': expiry_date,
                    'mrp': mrp,
                    'cost_price': cost_price,
                    'sale_price': mrp  # Default sale price to MRP if not specified
                }
            )
            # Update fields if batch exists but new info provided
            if not created:
                 if expiry_date: batch.expiry_date = expiry_date
                 if mrp: batch.mrp = mrp
                 batch.sale_price = mrp # Update sale price to MRP
                 batch.save()
            
            data['batch'] = batch

        return super().to_internal_value(data)

class PurchaseBillSerializer(serializers.ModelSerializer):
    items = PurchaseBillItemSerializer(many=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    meta = TransactionMetaSerializer(required=False)
    vendor_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = PurchaseBill
        fields = [
            'id', 'bill_number', 'bill_date', 'due_date',
            'vendor', 'vendor_name', 'vendor_display', 'vendor_address', 'vendor_gstin', 'gst_treatment',
            'warehouse', 'journal',
            'total_amount', 'amount_paid', 'payment_status', 'created_by', 'created_at', 'items', 'meta'
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'amount_paid', 'payment_status']

    def get_vendor_display(self, obj):
        return obj.vendor.name if obj.vendor else obj.vendor_name

    def validate(self, data):
        items = data.get('items')
        if self.instance is None and (items is None or len(items) == 0):
            raise serializers.ValidationError({'items': 'At least one item is required.'})
        return data

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        meta_data = validated_data.pop('meta', None)
        user = self.context['request'].user
        
        # Smart Feature: Auto-create/sync vendor details
        vendor_name = validated_data.get('vendor_name')
        vendor_address = validated_data.get('vendor_address')
        vendor_gstin = validated_data.get('vendor_gstin')
        
        if vendor_name:
            try:
                vendor = Customer.objects.get(name=vendor_name, created_by=user)
                updated = False
                if vendor_address and not vendor.address:
                    vendor.address = vendor_address
                    updated = True
                if vendor_gstin and not vendor.gstin:
                    vendor.gstin = vendor_gstin
                    updated = True
                if updated:
                    vendor.save()
            except Customer.DoesNotExist:
                vendor = Customer.objects.create(
                    name=vendor_name,
                    address=vendor_address,
                    gstin=vendor_gstin,
                    created_by=user
                )
                from .models_sidecar import PartyMeta
                PartyMeta.objects.get_or_create(customer=vendor, defaults={'party_category': 'vendor'})
        
        # Fix 500 Error: explicitly pass created_by to avoid IntegrityError
        validated_data['created_by'] = user
        
        purchase_bill = PurchaseBill.objects.create(**validated_data)
        for item_data in items_data:
            PurchaseBillItem.objects.create(purchase_bill=purchase_bill, **item_data)
        return purchase_bill

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        user = self.context['request'].user
        
        # Smart Feature: Auto-create/sync vendor details
        vendor_name = validated_data.get('vendor_name', instance.vendor_name)
        vendor_address = validated_data.get('vendor_address', instance.vendor_address)
        vendor_gstin = validated_data.get('vendor_gstin', instance.vendor_gstin)
        
        if vendor_name:
            try:
                vendor = Customer.objects.get(name=vendor_name, created_by=user)
                updated = False
                if vendor_address and not vendor.address:
                    vendor.address = vendor_address
                    updated = True
                if vendor_gstin and not vendor.gstin:
                    vendor.gstin = vendor_gstin
                    updated = True
                if updated:
                    vendor.save()
            except Customer.DoesNotExist:
                vendor = Customer.objects.create(
                    name=vendor_name,
                    address=vendor_address,
                    gstin=vendor_gstin,
                    created_by=user
                )
                from .models_sidecar import PartyMeta
                PartyMeta.objects.get_or_create(customer=vendor, defaults={'party_category': 'vendor'})
        
        # Update the purchase bill fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Delete existing items and create new ones
        if items_data:
            instance.items.all().delete()
            for item_data in items_data:
                PurchaseBillItem.objects.create(purchase_bill=instance, **item_data)

            recalculated_total = sum((item.amount for item in instance.items.all()), Decimal('0'))
            instance.total_amount = recalculated_total

            if instance.amount_paid > instance.total_amount:
                instance.amount_paid = instance.total_amount

            instance.refresh_payment_status(save=False)
            instance.save(update_fields=['total_amount', 'amount_paid', 'payment_status'])
        
        return instance

class SalesInvoiceItemSerializer(serializers.ModelSerializer):
    product = ProductField()
    product_detail = serializers.SerializerMethodField(read_only=True)
    hsn_sac_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    free_quantity = serializers.IntegerField(min_value=0, required=False, default=0)
    price = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)
    discount = serializers.DecimalField(required=False, allow_null=True, default=0, max_digits=8, decimal_places=2)
    tax = serializers.DecimalField(required=False, allow_null=True, default=0, max_digits=8, decimal_places=2)
    amount = serializers.DecimalField(required=False, allow_null=True, max_digits=12, decimal_places=2)
    batch = serializers.PrimaryKeyRelatedField(queryset=ProductBatch.objects.all(), required=False, allow_null=True)

    class Meta:
        model = SalesInvoiceItem
        fields = ['id', 'product', 'product_detail', 'hsn_sac_code', 'unit', 'quantity', 'free_quantity', 'price', 'discount', 'tax', 'amount', 'batch']

    def get_product_detail(self, obj):
        return {
            "id": str(obj.product.id),
            "name": obj.product.name,
            "hsn_sac_code": obj.product.hsn_sac_code,
            "unit": obj.product.unit,
        }

    def to_internal_value(self, data):
        print("DEBUG SalesInvoiceItemSerializer: Processing data:", data)
        product_value = data.get('product')
        print("DEBUG SalesInvoiceItemSerializer: Product value:", product_value, type(product_value))
        
        if not product_value or not str(product_value).strip():
            error_msg = 'Product is required.'
            print("DEBUG SalesInvoiceItemSerializer: Error -", error_msg)
            raise serializers.ValidationError({'product': error_msg})
        
        # Get user from context
        request = self.context.get('request')
        print("DEBUG SalesInvoiceItemSerializer: Request context:", request)
        if not request or not hasattr(request, 'user'):
            error_msg = 'Authentication required.'
            print("DEBUG SalesInvoiceItemSerializer: Error -", error_msg)
            raise serializers.ValidationError({'product': error_msg})
        user = request.user
        print("DEBUG SalesInvoiceItemSerializer: User:", user)

        try:
            # Try to find product by UUID first
            print("DEBUG SalesInvoiceItemSerializer: Trying to parse as UUID:", product_value)
            uuid_obj = uuid.UUID(str(product_value))
            print("DEBUG SalesInvoiceItemSerializer: Valid UUID, looking for product:", uuid_obj)
            try:
                product = Product.objects.get(id=uuid_obj)
                print("DEBUG SalesInvoiceItemSerializer: Found existing product by UUID:", product.name)
                # Update product fields if present in data
                updated = False
                for field in ['hsn_sac_code', 'unit', 'price', 'tax']:
                    if field in data and data[field] is not None:
                        setattr(product, field, data[field])
                        updated = True
                if updated:
                    print("DEBUG SalesInvoiceItemSerializer: Updating product fields")
                    product.save()
            except Product.DoesNotExist:
                error_msg = f'Product with UUID {uuid_obj} does not exist.'
                print("DEBUG SalesInvoiceItemSerializer: Error -", error_msg)
                raise serializers.ValidationError({'product': error_msg})
        except ValueError:
            # Not a UUID, try to find or create by name
            print("DEBUG SalesInvoiceItemSerializer: Not a UUID, treating as product name:", product_value)
            try:
                product = Product.objects.get(name=product_value, created_by=user)
                print("DEBUG SalesInvoiceItemSerializer: Found existing product by name:", product.name)
                # Update existing product fields if present in data
                updated = False
                for field in ['hsn_sac_code', 'unit', 'price', 'tax']:
                    if field in data and data[field] is not None:
                        setattr(product, field, data[field])
                        updated = True
                if updated:
                    print("DEBUG SalesInvoiceItemSerializer: Updating existing product fields")
                    product.save()
            except Product.DoesNotExist:
                # Create new product
                print("DEBUG SalesInvoiceItemSerializer: Creating new product:", product_value)
                try:
                    product = Product.objects.create(
                        name=product_value,
                        hsn_sac_code=data.get('hsn_sac_code', ''),
                        unit=data.get('unit', 'pcs'),
                        price=data.get('price', 0),
                        tax=data.get('tax', 0),
                        created_by=user,
                    )
                    print("DEBUG SalesInvoiceItemSerializer: New product created:", product.id)
                except Exception as e:
                    error_msg = f'Failed to create product: {str(e)}'
                    print("DEBUG SalesInvoiceItemSerializer: Error creating product -", error_msg)
                    raise serializers.ValidationError({'product': error_msg})
        except Exception as e:
            error_msg = f'Unexpected error processing product: {str(e)}'
            print("DEBUG SalesInvoiceItemSerializer: Unexpected error -", error_msg)
            raise serializers.ValidationError({'product': error_msg})
            
        data['product'] = product
        
        # Handle null values for discount and tax - convert to defaults
        if data.get('discount') is None:
            data['discount'] = 0
        if data.get('tax') is None:
            data['tax'] = 0
            
        # FEFO Logic: Auto-select batch if not provided
        if 'batch' not in data and product:
            print(f"DEBUG SalesInvoiceItemSerializer: No batch provided for {product.name}, attempting FEFO selection")
            # Find batch with earliest expiry that has stock > 0
            # Note: We filter by is_active=True to properly exclude deleted/blocked batches
            best_batch = ProductBatch.objects.filter(
                product=product, 
                is_active=True,
                stock_points__quantity__gt=0
            ).order_by('expiry_date').first()
            
            if best_batch:
                print(f"DEBUG SalesInvoiceItemSerializer: FEFO selected batch {best_batch.batch_number} (Exp: {best_batch.expiry_date})")
                data['batch'] = best_batch.id
            else:
                 # Fallback: Try to find ANY active batch, even if stock is 0 (to record the sale against something)
                 # Or just leave it null (Global stock)
                 print("DEBUG SalesInvoiceItemSerializer: No suitable batch found with stock. Checking any active batch.")
                 any_batch = ProductBatch.objects.filter(product=product, is_active=True).order_by('expiry_date').first()
                 if any_batch:
                     data['batch'] = any_batch.id
                     print(f"DEBUG SalesInvoiceItemSerializer: Fallback selected batch {any_batch.batch_number}")

        print("DEBUG SalesInvoiceItemSerializer: Product processed successfully, calling super()")
        print("DEBUG SalesInvoiceItemSerializer: Final data before super():", data)
        try:
            result = super().to_internal_value(data)
            print("DEBUG SalesInvoiceItemSerializer: Super call successful")
            return result
        except Exception as e:
            error_msg = f'Error in parent serializer validation: {str(e)}'
            print("DEBUG SalesInvoiceItemSerializer: Super call error -", error_msg)
            raise serializers.ValidationError({'non_field_errors': [error_msg]})

class SalesInvoiceSerializer(serializers.ModelSerializer):
    items = SalesInvoiceItemSerializer(many=True, required=False)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    customer_name = serializers.CharField(required=False, allow_blank=True)  # Name to display, optional for draft
    status = serializers.CharField(required=False, default='final')
    customer_email = serializers.EmailField(write_only=True, required=False)
    customer_phone = serializers.CharField(write_only=True, required=False)
    customer_address = serializers.CharField(write_only=True, required=False)
    invoice_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    invoice_date = serializers.DateField(required=False, allow_null=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    meta = TransactionMetaSerializer(required=False)

    class Meta:
        model = SalesInvoice
        # Exclude 'customer' from fields to avoid UUID validation issues
        fields = ['id', 'customer_name', 'customer_email', 'customer_phone', 'customer_address', 
                  'invoice_number', 'invoice_date', 'due_date', 'delivery_address', 'place_of_supply', 'gst_treatment',
                  'journal', 'warehouse', 'status', 'total_amount', 'amount_paid', 'payment_status', 'created_by', 'created_at', 'items', 'meta']

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

    def validate(self, data):
        """
        Check for Credit Limit violations.
        """
        request = self.context.get('request')
        user = request.user if request else None
        
        # We need to resolve the customer to check their limit
        # Use the customer object resolved in to_internal_value
        customer = getattr(self, '_customer_obj', None)
        
        # Fallback: Try to resolve by email if _customer_obj is missing but email is in data
        # (This happens if to_internal_value didn't run or didn't find it, but we shouldn't rely on it)
        if not customer:
             customer_email = data.get('customer_email')
             if customer_email and user:
                 try:
                     customer = Customer.objects.get(email=customer_email, created_by=user)
                 except Customer.DoesNotExist:
                     pass

        total_amount = data.get('total_amount', getattr(self.instance, 'total_amount', 0))
        items = data.get('items')
        if items:
            total_amount = sum(
                (self._calculate_line_amount(item) for item in items),
                Decimal('0.00')
            )

        if self.instance and self.instance.payment_status in ['partial_paid', 'paid']:
            immutable_fields = ['invoice_number', 'invoice_date', 'customer_name', 'place_of_supply']
            immutable_errors = {}
            for field in immutable_fields:
                if field in data and data[field] != getattr(self.instance, field):
                    immutable_errors[field] = f'{field} cannot be changed after payment has been recorded.'
            if immutable_errors:
                raise serializers.ValidationError(immutable_errors)
        
        if customer:
            if not customer.allow_credit:
                # Strict Check: If allow_credit is False, STRICTLY enforce the limit
                # We interpret allow_credit=False as "Credit Limit is Enforced"
                # If allow_credit=True, we might allow overriding (blocking warning vs error)
                # For now, simplistic login:
                
                # Check current balance (Owes us positive) + New Bill
                new_balance = customer.current_balance + total_amount
                
                # Debug print
                print(f"DEBUG Credit Check: Customer={customer.name}, Bal={customer.current_balance}, Limit={customer.credit_limit}, New={new_balance}")
                
                if new_balance > customer.credit_limit:
                    raise serializers.ValidationError(
                        f"Credit Limit Exceeded! Current Balance: {customer.current_balance}, Limit: {customer.credit_limit}. "
                        f"This invoice would take balance to {new_balance}."
                    )
        return data

    def to_internal_value(self, data):
        print("DEBUG SalesInvoiceSerializer: Processing data:", data)
        
        # Handle legacy 'customer' field for backward compatibility
        if 'customer' in data and 'customer_name' not in data:
            data['customer_name'] = data['customer']
        
        status_value = data.get('status', getattr(self.instance, 'status', 'final'))
        customer_name = data.get('customer_name')
        if self.instance and (not customer_name or not str(customer_name).strip()):
            related_customer = getattr(self.instance, 'customer', None)
            if related_customer and related_customer.name:
                customer_name = related_customer.name
        if self.instance and (not customer_name or not str(customer_name).strip()):
            customer_name = self.instance.customer_name or ''
        customer_name = customer_name or ''
        customer_email = data.get('customer_email', '')
        customer_phone = data.get('customer_phone', '')
        customer_address = data.get('customer_address', '')
        
        print("DEBUG SalesInvoiceSerializer: Customer name:", customer_name)
        print("DEBUG SalesInvoiceSerializer: Customer email:", customer_email)
        
        if not customer_name.strip() and status_value != 'draft':
            error_msg = 'Customer name is required.'
            print("DEBUG SalesInvoiceSerializer: Error -", error_msg)
            raise serializers.ValidationError({'customer_name': error_msg})
        
        # Get user from context
        request = self.context.get('request')
        if not request or not hasattr(request, 'user'):
            error_msg = 'Authentication required.'
            print("DEBUG SalesInvoiceSerializer: Error -", error_msg)
            raise serializers.ValidationError({'customer_name': error_msg})
        user = request.user
        print("DEBUG SalesInvoiceSerializer: User:", user)

        customer_obj = getattr(self.instance, 'customer', None) if self.instance else None
        
        # Only create/find Customer object if email is provided
        if customer_email and customer_email.strip():
            print("DEBUG SalesInvoiceSerializer: Email provided, will create/find Customer object")
            try:
                # Try to find customer by email
                customer_obj = Customer.objects.get(email=customer_email, created_by=user)
                print("DEBUG SalesInvoiceSerializer: Found existing customer by email:", customer_obj.name)
                # Update customer name if different
                if customer_obj.name != customer_name:
                    customer_obj.name = customer_name
                    if customer_phone:
                        customer_obj.phone = customer_phone
                    if customer_address:
                        customer_obj.address = customer_address
                    customer_obj.save()
                    print("DEBUG SalesInvoiceSerializer: Updated customer details")
            except Customer.DoesNotExist:
                # Create new customer with email
                print("DEBUG SalesInvoiceSerializer: Creating new customer with email:", customer_name)
                try:
                    customer_obj = Customer.objects.create(
                        name=customer_name,
                        email=customer_email,
                        phone=customer_phone,
                        address=customer_address,
                        created_by=user,
                    )
                    print("DEBUG SalesInvoiceSerializer: New customer created:", customer_obj.id)
                except Exception as e:
                    error_msg = f'Failed to create customer: {str(e)}'
                    print("DEBUG SalesInvoiceSerializer: Error creating customer -", error_msg)
                    raise serializers.ValidationError({'customer_email': error_msg})
        else:
            print("DEBUG SalesInvoiceSerializer: No email provided, will not create Customer object")
            
        # Store customer object separately - don't pass to parent validation
        self._customer_obj = customer_obj  # Store for use in create method
        
        # Remove customer fields that aren't in Meta.fields
        temp_data = data.copy()
        temp_data['customer_name'] = customer_name
        if 'place_of_supply' in temp_data:
            temp_data['place_of_supply'] = normalize_indian_state_choice(temp_data.get('place_of_supply'))
        temp_data.pop('customer_email', None)
        temp_data.pop('customer_phone', None) 
        temp_data.pop('customer_address', None)
        # Don't set customer field since it's not in Meta.fields anymore
        
        print("DEBUG SalesInvoiceSerializer: Customer processing completed, calling super()")
        try:
            result = super().to_internal_value(temp_data)
            print("DEBUG SalesInvoiceSerializer: Super call successful")
            return result
        except Exception as e:
            error_msg = f'Error in parent serializer validation: {str(e)}'
            print("DEBUG SalesInvoiceSerializer: Super call error -", error_msg)
            raise serializers.ValidationError({'non_field_errors': [error_msg]})

    def create(self, validated_data):
        print("DEBUG SalesInvoiceSerializer: Creating sales invoice with data:", validated_data)
        items_data = validated_data.pop('items')
        meta_data = validated_data.pop('meta', None)
        provided_total_amount = validated_data.pop('total_amount', None)
        print("DEBUG SalesInvoiceSerializer: Items data:", items_data)

        recalculated_total = sum(
            (self._calculate_line_amount(item) for item in items_data),
            Decimal('0.00')
        ) if items_data else Decimal(str(provided_total_amount or '0.00'))
        validated_data['total_amount'] = recalculated_total
        
        # Add the customer object that we stored earlier
        customer_obj = getattr(self, '_customer_obj', None)
        validated_data['customer'] = customer_obj
        print("DEBUG SalesInvoiceSerializer: Added customer object:", customer_obj)
        
        # Smart Tax: Auto-set Place of Supply if not provided
        if not validated_data.get('place_of_supply') and customer_obj and customer_obj.state:
            validated_data['place_of_supply'] = customer_obj.state
            print(f"DEBUG SalesInvoiceSerializer: Auto-set POS to {customer_obj.state}")
            
        try:
            sales_invoice = SalesInvoice.objects.create(**validated_data)
            print("DEBUG SalesInvoiceSerializer: Sales invoice created:", sales_invoice.id)
            
            for i, item_data in enumerate(items_data):
                item_data['amount'] = self._calculate_line_amount(item_data)
                print(f"DEBUG SalesInvoiceSerializer: Creating item {i+1}:", item_data)
                SalesInvoiceItem.objects.create(sales_invoice=sales_invoice, **item_data)
                print(f"DEBUG SalesInvoiceSerializer: Item {i+1} created successfully")
            
            print("DEBUG SalesInvoiceSerializer: All items created successfully")

            recalculated_total = sum((item.amount for item in sales_invoice.items.all()), Decimal('0'))
            sales_invoice.total_amount = recalculated_total
            if sales_invoice.amount_paid > sales_invoice.total_amount:
                sales_invoice.amount_paid = sales_invoice.total_amount
            sales_invoice.refresh_payment_status(save=False)
            sales_invoice.save(update_fields=['total_amount', 'amount_paid', 'payment_status'])
            
            # Create Transaction Meta
            if meta_data:
                TransactionMeta.objects.create(invoice=sales_invoice, **meta_data)
            else:
                TransactionMeta.objects.create(invoice=sales_invoice)
                
            # Feature 28: Loyalty Points Accrual (1 Point per ₹100)
            if customer_obj and hasattr(customer_obj, 'meta'):
                try:
                    points_earned = int(sales_invoice.total_amount / 100)
                    if points_earned > 0:
                        customer_obj.meta.loyalty_points += points_earned
                        customer_obj.meta.save()
                        print(f"DEBUG: Accrued {points_earned} loyalty points for {customer_obj.name}")
                except Exception as e:
                     print(f"DEBUG: Failed to accrue loyalty points: {e}")
                
            return sales_invoice
        except Exception as e:
            print("DEBUG SalesInvoiceSerializer: Error in create method:", str(e))
            import traceback
            traceback.print_exc()
            raise

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        validated_data.pop('total_amount', None)
        meta_data = validated_data.pop('meta', None)
        
        # Update the sales invoice fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Delete existing items and create new ones when provided
        if items_data:
            instance.items.all().delete()
            for item_data in items_data:
                item_data['amount'] = self._calculate_line_amount(item_data)
                SalesInvoiceItem.objects.create(sales_invoice=instance, **item_data)

            recalculated_total = sum((item.amount for item in instance.items.all()), Decimal('0'))
            instance.total_amount = recalculated_total

        if instance.amount_paid > instance.total_amount:
            instance.amount_paid = instance.total_amount

        instance.refresh_payment_status(save=False)
        instance.save(update_fields=['total_amount', 'amount_paid', 'payment_status'])
        
        if meta_data:
            meta, created = TransactionMeta.objects.get_or_create(invoice=instance)
            for attr, value in meta_data.items():
                setattr(meta, attr, value)
            meta.save()

        return instance

from .serializers_sidecar import PartyMetaSerializer
from .models_sidecar import PartyMeta

class VendorSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    meta = PartyMetaSerializer(required=False)
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'name', 'email', 'phone', 'gstin', 'address', 'state',
            'created_by', 'created_at', 'meta'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate_email(self, value):
        if value:
            # Check for duplicate email within the same user's vendors
            user = self.context['request'].user
            queryset = Vendor.objects.filter(email=value, created_by=user)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError("A vendor with this email already exists.")
        return value

    def create(self, validated_data):
        meta_data = validated_data.pop('meta', None)
        validated_data['created_by'] = self.context['request'].user
        
        vendor = Vendor.objects.create(**validated_data)
        
        if meta_data:
            PartyMeta.objects.create(vendor=vendor, **meta_data)
        else:
            PartyMeta.objects.create(vendor=vendor)
            
        return vendor

    def update(self, instance, validated_data):
        meta_data = validated_data.pop('meta', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if meta_data:
            meta, _ = PartyMeta.objects.get_or_create(vendor=instance)
            for attr, value in meta_data.items():
                setattr(meta, attr, value)
            meta.save()
            
        return instance
    

class CustomerSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    meta = PartyMetaSerializer(required=False)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'email', 'phone', 'gstin', 'address', 'state',
            'credit_limit', 'current_balance', 'allow_credit',
            'created_by', 'created_at', 'meta'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate_email(self, value):
        if value:
            # Check for duplicate email within the same user's customers
            user = self.context['request'].user
            queryset = Customer.objects.filter(email=value, created_by=user)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError("A customer with this email already exists.")
        return value

    def create(self, validated_data):
        meta_data = validated_data.pop('meta', None)
        validated_data['created_by'] = self.context['request'].user
        
        customer = super().create(validated_data)
        
        if meta_data:
            PartyMeta.objects.create(customer=customer, **meta_data)
        else:
            PartyMeta.objects.create(customer=customer)
            
        return customer

    def update(self, instance, validated_data):
        meta_data = validated_data.pop('meta', None)
        
        # Update customer fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update meta fields
        if meta_data:
            meta, created = PartyMeta.objects.get_or_create(customer=instance)
            for attr, value in meta_data.items():
                setattr(meta, attr, value)
            meta.save()
            
        return instance

class PaymentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'customer', 'customer_name', 'invoice', 'date', 'amount',
            'mode', 'reference', 'notes', 'created_by', 'created_at'
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate(self, attrs):
        customer = attrs.get('customer') or getattr(self.instance, 'customer', None)
        invoice = attrs.get('invoice', getattr(self.instance, 'invoice', None))
        amount = Decimal(str(attrs.get('amount', getattr(self.instance, 'amount', 0) or 0)))

        if amount <= 0:
            raise serializers.ValidationError({'amount': 'Amount must be greater than 0.'})

        if invoice:
            if customer and invoice.customer_id != customer.id:
                raise serializers.ValidationError({'invoice': 'Selected invoice does not belong to the selected customer.'})

            outstanding = Decimal(str(invoice.total_amount or 0)) - Decimal(str(invoice.amount_paid or 0))
            # On update, current payment is already included in invoice.amount_paid if linked to same invoice.
            if self.instance and self.instance.invoice_id == invoice.id:
                outstanding += Decimal(str(self.instance.amount or 0))

            if amount > outstanding:
                raise serializers.ValidationError({
                    'amount': f'Amount exceeds invoice outstanding (max {outstanding:.2f}).'
                })

        return attrs

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
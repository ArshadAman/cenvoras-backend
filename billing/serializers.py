from rest_framework import serializers
from .models import PurchaseBill, PurchaseBillItem, SalesInvoice, SalesInvoiceItem, Customer
from inventory.models import Product
import uuid

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
    product = ProductField()
    product_detail = serializers.SerializerMethodField(read_only=True)
    hsn_sac_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    unit = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=2)
    discount = serializers.DecimalField(required=False, allow_null=True, default=0, max_digits=8, decimal_places=2)
    tax = serializers.DecimalField(required=False, allow_null=True, default=0, max_digits=8, decimal_places=2)
    amount = serializers.DecimalField(required=False, allow_null=True, max_digits=12, decimal_places=2)

    class Meta:
        model = SalesInvoiceItem
        fields = ['id', 'product', 'product_detail', 'hsn_sac_code', 'unit', 'quantity', 'price', 'discount', 'tax', 'amount']

    def get_product_detail(self, obj):
        return {
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
    items = SalesInvoiceItemSerializer(many=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    customer_name = serializers.CharField()  # Always required - the name to display
    customer_email = serializers.EmailField(write_only=True, required=False)
    customer_phone = serializers.CharField(write_only=True, required=False)
    customer_address = serializers.CharField(write_only=True, required=False)
    invoice_number = serializers.CharField(max_length=100)
    invoice_date = serializers.DateField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        model = SalesInvoice
        # Exclude 'customer' from fields to avoid UUID validation issues
        fields = ['id', 'customer_name', 'customer_email', 'customer_phone', 'customer_address', 
                  'invoice_number', 'invoice_date', 'due_date', 'delivery_address', 'gst_treatment',
                  'journal', 'total_amount', 'created_by', 'created_at', 'items']

    def to_internal_value(self, data):
        print("DEBUG SalesInvoiceSerializer: Processing data:", data)
        
        # Handle legacy 'customer' field for backward compatibility
        if 'customer' in data and 'customer_name' not in data:
            data['customer_name'] = data['customer']
        
        customer_name = data.get('customer_name')
        customer_email = data.get('customer_email', '')
        customer_phone = data.get('customer_phone', '')
        customer_address = data.get('customer_address', '')
        
        print("DEBUG SalesInvoiceSerializer: Customer name:", customer_name)
        print("DEBUG SalesInvoiceSerializer: Customer email:", customer_email)
        
        if not customer_name or not str(customer_name).strip():
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

        customer_obj = None
        
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
        print("DEBUG SalesInvoiceSerializer: Items data:", items_data)
        
        # Add the customer object that we stored earlier
        customer_obj = getattr(self, '_customer_obj', None)
        validated_data['customer'] = customer_obj
        print("DEBUG SalesInvoiceSerializer: Added customer object:", customer_obj)
        
        try:
            sales_invoice = SalesInvoice.objects.create(**validated_data)
            print("DEBUG SalesInvoiceSerializer: Sales invoice created:", sales_invoice.id)
            
            for i, item_data in enumerate(items_data):
                print(f"DEBUG SalesInvoiceSerializer: Creating item {i+1}:", item_data)
                SalesInvoiceItem.objects.create(sales_invoice=sales_invoice, **item_data)
                print(f"DEBUG SalesInvoiceSerializer: Item {i+1} created successfully")
            
            print("DEBUG SalesInvoiceSerializer: All items created successfully")
            return sales_invoice
        except Exception as e:
            print("DEBUG SalesInvoiceSerializer: Error in create method:", str(e))
            import traceback
            traceback.print_exc()
            raise

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        
        # Update the sales invoice fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Delete existing items and create new ones
        instance.items.all().delete()
        for item_data in items_data:
            SalesInvoiceItem.objects.create(sales_invoice=instance, **item_data)
        
        return instance
    


class CustomerSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'email', 'phone', 'gstin', 'address', 
            'created_by', 'created_at'
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
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
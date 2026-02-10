from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.utils import timezone
from .authentication import ApiKeyAuthentication
from rest_framework.permissions import IsAuthenticated
from inventory.models import Product
from billing.models import SalesInvoice, SalesInvoiceItem, Customer, Payment
from inventory.serializers import ProductSerializer
from billing.serializers import SalesInvoiceSerializer # You might need a specific serializer for input
import uuid 

class PublicProductListView(generics.ListAPIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer

    def get_queryset(self):
        # Filter products by the user associated with the API Key
        # Only show products with positive price (assuming those are for sale)
        return Product.objects.filter(created_by=self.request.user, sale_price__gt=0)

class PublicOrderCreateView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        data = request.data
        user = request.user
        
        # 1. Customer Sync
        customer_data = data.get('customer', {})
        phone = customer_data.get('phone')
        email = customer_data.get('email')
        
        if not phone and not email:
             return Response({"error": "Customer phone or email is required"}, status=status.HTTP_400_BAD_REQUEST)

        customer = None
        if phone:
            customer = Customer.objects.filter(created_by=user, phone=phone).first()
        if not customer and email:
            customer = Customer.objects.filter(created_by=user, email=email).first()
            
        if not customer:
            # Create new customer
            customer = Customer.objects.create(
                created_by=user,
                name=customer_data.get('name', 'Online Customer'),
                phone=phone,
                email=email,
                address=customer_data.get('address', ''),
                state=customer_data.get('state', None) # Ensure valid state code if provided
            )
        
        # 2. Invoice Creation
        items_data = data.get('items', [])
        if not items_data:
            return Response({"error": "No items provided"}, status=status.HTTP_400_BAD_REQUEST)
            
        invoice = SalesInvoice.objects.create(
            created_by=user,
            customer=customer,
            customer_name=customer.name,
            invoice_number=f"WEB-{uuid.uuid4().hex[:8].upper()}", # temporary ID logic
            invoice_date=data.get('date', None) or timezone.now().date(),
            total_amount=0 # Will calculate
        )
        
        total_amount = 0
        
        for item in items_data:
            try:
                product = Product.objects.get(id=item['product_id'], created_by=user)
            except Product.DoesNotExist:
                return Response({"error": f"Product {item.get('product_id')} not found"}, status=status.HTTP_400_BAD_REQUEST)
                
            qty = int(item.get('quantity', 1))
            price = product.sale_price # or item.get('price') if trusting external source
            amount = price * qty
            
            SalesInvoiceItem.objects.create(
                sales_invoice=invoice,
                product=product,
                quantity=qty,
                price=price,
                amount=amount
            )
            total_amount += amount
            
            # Stock reduction logic should ideally be triggered here or via signals
            # checking if product has enough stock is also a good idea
            
        invoice.total_amount = total_amount
        invoice.save()
        
        # Update customer balance (Debit)
        customer.current_balance += total_amount
        customer.save()
        
        # 3. Payment Recording
        payment_status = data.get('payment_status', 'unpaid')
        if payment_status.lower() == 'paid':
            Payment.objects.create(
                created_by=user,
                customer=customer,
                date=invoice.invoice_date,
                amount=total_amount,
                mode=data.get('payment_mode', 'online'), # e.g. 'upi', 'card'
                reference=data.get('transaction_id', f"INV-{invoice.invoice_number}"),
                notes="Auto-generated from Online Order"
            )
            # Update customer balance (Credit)
            customer.current_balance -= total_amount
            customer.save()
            
        return Response({
            "message": "Order created successfully", 
            "invoice_id": invoice.id,
            "customer_id": customer.id
        }, status=status.HTTP_201_CREATED)

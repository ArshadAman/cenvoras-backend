from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.http import HttpResponse
import uuid
import csv
import io
import zipfile
from decimal import Decimal, InvalidOperation
from django.utils.dateparse import parse_date

from .authentication import ApiKeyAuthentication
from .models import ApiKey, NotificationLog, NotificationTemplate
from .serializers import (
    ApiKeySerializer, NotificationLogSerializer, NotificationTemplateSerializer,
    SendInvoiceNotificationSerializer
)
from .notifications import send_invoice_notification, send_email, send_whatsapp
from users.permissions import IsAdminUser, IsManagerOrAdmin

from inventory.models import Product
from inventory.serializers import ProductSerializer
from billing.models import SalesInvoice, SalesInvoiceItem, Customer, Payment



# =============================================================================
# Existing API Key Integration Views
# =============================================================================

class PublicProductListView(generics.ListAPIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(created_by=self.request.user, sale_price__gt=0)


class PublicOrderCreateView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        data = request.data
        user = request.user
        
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
            customer = Customer.objects.create(
                created_by=user,
                name=customer_data.get('name', 'Online Customer'),
                phone=phone, email=email,
                address=customer_data.get('address', ''),
                state=customer_data.get('state', None)
            )
        
        items_data = data.get('items', [])
        if not items_data:
            return Response({"error": "No items provided"}, status=status.HTTP_400_BAD_REQUEST)
            
        invoice = SalesInvoice.objects.create(
            created_by=user, customer=customer, customer_name=customer.name,
            invoice_number=f"WEB-{uuid.uuid4().hex[:8].upper()}",
            invoice_date=data.get('date', None) or timezone.now().date(),
            total_amount=0
        )
        
        total_amount = 0
        for item in items_data:
            try:
                product = Product.objects.get(id=item['product_id'], created_by=user)
            except Product.DoesNotExist:
                return Response({"error": f"Product {item.get('product_id')} not found"}, status=status.HTTP_400_BAD_REQUEST)
                
            qty = int(item.get('quantity', 1))
            price = product.sale_price
            amount = price * qty
            SalesInvoiceItem.objects.create(
                sales_invoice=invoice, product=product,
                quantity=qty, price=price, amount=amount
            )
            total_amount += amount
            
        invoice.total_amount = total_amount
        invoice.save()
        
        customer.current_balance += total_amount
        customer.save()
        
        payment_status = data.get('payment_status', 'unpaid')
        if payment_status.lower() == 'paid':
            Payment.objects.create(
                created_by=user, customer=customer,
                date=invoice.invoice_date, amount=total_amount,
                mode=data.get('payment_mode', 'online'),
                reference=data.get('transaction_id', f"INV-{invoice.invoice_number}"),
                notes="Auto-generated from Online Order"
            )
            customer.current_balance -= total_amount
            customer.save()
            
        return Response({
            "message": "Order created successfully",
            "invoice_id": invoice.id,
            "customer_id": customer.id
        }, status=status.HTTP_201_CREATED)


# =============================================================================
# Notification Views
# =============================================================================

class SendInvoiceNotificationView(APIView):
    """POST: Send invoice to customer via email/WhatsApp."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SendInvoiceNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invoice_id = serializer.validated_data['invoice_id']
        channels = serializer.validated_data.get('channels', ['email'])

        try:
            invoice = SalesInvoice.objects.get(id=invoice_id, created_by=request.user)
        except SalesInvoice.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        results = send_invoice_notification(request.user, invoice, channels)
        return Response({"message": "Notification processed", "results": results})


class SendCustomEmailView(APIView):
    """POST: Send a one-off custom email."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        recipient = request.data.get('recipient', '')
        subject = request.data.get('subject', '').strip() or 'Message from Cenvora'
        body = request.data.get('body', '')
        if not recipient or not body:
            return Response({'error': 'recipient and body are required'}, status=status.HTTP_400_BAD_REQUEST)
        result = send_email(
            request.user, recipient, subject, body,
            related_model='custom', related_id=''
        )
        return Response({'message': 'Email queued', 'result': result})


class SendPaymentRemindersView(APIView):
    """POST: Trigger bulk payment reminder emails for all customers with outstanding balance."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .tasks import send_payment_reminders_for_user
        overdue_days = request.data.get('overdue_days', 30)
        stagger_seconds = 2
        # Count eligible before dispatching
        count = Customer.objects.filter(
            created_by=request.user,
            current_balance__gt=0,
        ).exclude(email='').count()
        send_payment_reminders_for_user.delay(str(request.user.id), overdue_days)
        total_dispatch_window_seconds = count * stagger_seconds
        return Response({
            'message': f'Payment reminders queued for {count} customer(s) with outstanding balance.',
            'queued': count,
            'stagger_seconds': stagger_seconds,
            'dispatch_window_seconds': total_dispatch_window_seconds,
        })


class NotificationLogListView(generics.ListAPIView):
    """GET: View tenant-scoped notification history."""
    serializer_class = NotificationLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)

        queryset = NotificationLog.objects.filter(
            Q(user=tenant) | Q(user__parent=tenant)
        )

        # Keep this dashboard customer-facing by excluding internal billing/status emails.
        queryset = queryset.exclude(
            related_model__in=['SubscriptionPayment', 'TenantSubscriptionExpiry']
        )

        if getattr(tenant, 'email', None):
            queryset = queryset.exclude(recipient__iexact=tenant.email)

        return queryset


class NotificationTemplateListView(generics.ListCreateAPIView):
    """GET/POST: Manage notification templates."""
    serializer_class = NotificationTemplateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return NotificationTemplate.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# =============================================================================
# Barcode Lookup
# =============================================================================

class BarcodeLookupView(APIView):
    """GET: Lookup product by barcode."""
    permission_classes = [IsAuthenticated]

    def get(self, request, barcode):
        try:
            meta = Product.objects.get(
                created_by=request.user,
                meta__barcode=barcode
            )
            return Response({
                "id": str(meta.id),
                "name": meta.name,
                "price": str(meta.price),
                "sale_price": str(meta.sale_price),
                "stock": meta.stock,
                "hsn_sac_code": meta.hsn_sac_code or '',
            })
        except Product.DoesNotExist:
            return Response({
                "error": "Product not found",
                "barcode": barcode
            }, status=status.HTTP_404_NOT_FOUND)


# =============================================================================
# Data Backup & Restore
# =============================================================================

class DataExportView(APIView):
    """GET: Export all user data as JSON."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request):
        user = request.user
        export_format = (request.query_params.get('format') or 'json').lower()
        
        products = list(Product.objects.filter(created_by=user).values(
            'id', 'name', 'description', 'price', 'sale_price', 'stock',
            'hsn_sac_code', 'unit', 'low_stock_alert'
        ))
        customers = list(Customer.objects.filter(created_by=user).values(
            'id', 'name', 'email', 'phone', 'gstin', 'address',
            'credit_limit', 'current_balance', 'state'
        ))
        invoices = list(SalesInvoice.objects.filter(created_by=user).values(
            'id', 'invoice_number', 'invoice_date', 'customer_name',
            'total_amount', 'place_of_supply'
        ))
        
        # Convert UUIDs and dates to strings
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        
        export_data = {
            "exported_at": str(timezone.now()),
            "business_name": user.business_name or user.username,
            "products": json.loads(json.dumps(products, cls=DjangoJSONEncoder)),
            "customers": json.loads(json.dumps(customers, cls=DjangoJSONEncoder)),
            "invoices": json.loads(json.dumps(invoices, cls=DjangoJSONEncoder)),
            "payments": json.loads(json.dumps(list(Payment.objects.filter(created_by=user).values(
                'id', 'date', 'amount', 'mode', 'reference', 'notes', 'customer_id', 'invoice_id'
            )), cls=DjangoJSONEncoder)),
            "summary": {
                "total_products": len(products),
                "total_customers": len(customers),
                "total_invoices": len(invoices),
            }
        }

        if export_format == 'csv':
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                def write_csv(file_name, rows, headers):
                    text_buffer = io.StringIO()
                    writer = csv.DictWriter(text_buffer, fieldnames=headers)
                    writer.writeheader()
                    for row in rows:
                        normalized = {k: row.get(k, '') for k in headers}
                        writer.writerow(normalized)
                    zf.writestr(file_name, text_buffer.getvalue())

                write_csv(
                    'products.csv',
                    export_data['products'],
                    ['id', 'name', 'description', 'price', 'sale_price', 'stock', 'hsn_sac_code', 'unit', 'low_stock_alert']
                )
                write_csv(
                    'customers.csv',
                    export_data['customers'],
                    ['id', 'name', 'email', 'phone', 'gstin', 'address', 'credit_limit', 'current_balance', 'state']
                )
                write_csv(
                    'invoices.csv',
                    export_data['invoices'],
                    ['id', 'invoice_number', 'invoice_date', 'customer_name', 'total_amount', 'place_of_supply']
                )
                write_csv(
                    'payments.csv',
                    export_data['payments'],
                    ['id', 'date', 'amount', 'mode', 'reference', 'notes', 'customer_id', 'invoice_id']
                )
                zf.writestr('summary.json', json.dumps(export_data['summary'], indent=2))

            buffer.seek(0)
            response = HttpResponse(buffer.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="cenvora-backup-{timezone.now().date()}.zip"'
            return response

        return Response(export_data)


class DataImportView(APIView):
    """POST: Import data from JSON backup."""
    permission_classes = [IsAdminUser]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request):
        import_format = (request.query_params.get('format') or request.data.get('format') or 'json').lower()
        data = request.data
        user = request.user
        imported = {"products": 0, "customers": 0, "invoices": 0, "payments": 0}

        def parse_decimal(value, default='0'):
            if value in [None, '']:
                return Decimal(default)
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return Decimal(default)

        if import_format == 'csv':
            upload = request.FILES.get('file')
            if not upload:
                return Response({'error': 'file is required for CSV import'}, status=status.HTTP_400_BAD_REQUEST)

            csv_payload = {'products': [], 'customers': [], 'invoices': [], 'payments': []}
            try:
                with zipfile.ZipFile(upload, 'r') as zf:
                    if 'products.csv' in zf.namelist():
                        with zf.open('products.csv') as f:
                            reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                            csv_payload['products'] = list(reader)
                    if 'customers.csv' in zf.namelist():
                        with zf.open('customers.csv') as f:
                            reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                            csv_payload['customers'] = list(reader)
                    if 'invoices.csv' in zf.namelist():
                        with zf.open('invoices.csv') as f:
                            reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                            csv_payload['invoices'] = list(reader)
                    if 'payments.csv' in zf.namelist():
                        with zf.open('payments.csv') as f:
                            reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig'))
                            csv_payload['payments'] = list(reader)
            except zipfile.BadZipFile:
                return Response({'error': 'Invalid ZIP file for CSV import'}, status=status.HTTP_400_BAD_REQUEST)

            data = csv_payload

        customer_id_map = {}
        invoice_id_map = {}

        # Import products
        for p in data.get('products', []):
            if not p.get('name'):
                continue
            Product.objects.update_or_create(
                created_by=user, name=p['name'],
                defaults={
                    'price': parse_decimal(p.get('price', 0)),
                    'sale_price': parse_decimal(p.get('sale_price', 0)) if p.get('sale_price') not in [None, ''] else None,
                    'hsn_sac_code': p.get('hsn_sac_code', ''),
                    'unit': p.get('unit', 'pcs'),
                    'description': p.get('description', ''),
                }
            )
            imported["products"] += 1

        # Import customers
        for c in data.get('customers', []):
            if not c.get('name'):
                continue
            customer, _ = Customer.objects.update_or_create(
                created_by=user, name=c['name'],
                defaults={
                    'email': c.get('email', ''),
                    'phone': c.get('phone', ''),
                    'gstin': c.get('gstin', ''),
                    'address': c.get('address', ''),
                    'credit_limit': parse_decimal(c.get('credit_limit', 0)),
                }
            )
            old_customer_id = c.get('id')
            if old_customer_id:
                customer_id_map[str(old_customer_id)] = customer
            imported["customers"] += 1

        # Import invoices
        for inv in data.get('invoices', []):
            if not inv.get('invoice_number'):
                continue

            invoice_date = parse_date(str(inv.get('invoice_date'))) or timezone.now().date()
            invoice, _ = SalesInvoice.objects.update_or_create(
                created_by=user,
                invoice_number=inv['invoice_number'],
                defaults={
                    'invoice_date': invoice_date,
                    'customer_name': inv.get('customer_name', 'Cash'),
                    'total_amount': parse_decimal(inv.get('total_amount', 0)),
                    'place_of_supply': inv.get('place_of_supply') or None,
                }
            )
            old_invoice_id = inv.get('id')
            if old_invoice_id:
                invoice_id_map[str(old_invoice_id)] = invoice
            imported["invoices"] += 1

        # Import payments
        for pay in data.get('payments', []):
            old_customer_id = str(pay.get('customer_id') or '')
            old_invoice_id = str(pay.get('invoice_id') or '')
            customer_obj = customer_id_map.get(old_customer_id)
            invoice_obj = invoice_id_map.get(old_invoice_id)

            if not customer_obj:
                continue

            payment_date = parse_date(str(pay.get('date'))) or timezone.now().date()
            amount = parse_decimal(pay.get('amount', 0))
            if amount <= 0:
                continue

            Payment.objects.create(
                created_by=user,
                customer=customer_obj,
                invoice=invoice_obj,
                date=payment_date,
                amount=amount,
                mode=pay.get('mode') or 'cash',
                reference=pay.get('reference') or '',
                notes=pay.get('notes') or '',
            )
            imported["payments"] += 1

        return Response({"message": "Import completed", "format": import_format, "imported": imported})


# =============================================================================
# API Key Management
# =============================================================================

class ApiKeyListCreateView(generics.ListCreateAPIView):
    serializer_class = ApiKeySerializer
    permission_classes = [IsManagerOrAdmin]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return ApiKey.objects.none()
        return ApiKey.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ApiKeyDeleteView(generics.DestroyAPIView):
    serializer_class = ApiKeySerializer
    permission_classes = [IsManagerOrAdmin]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return ApiKey.objects.none()
        return ApiKey.objects.filter(user=self.request.user)

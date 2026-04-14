from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import PurchaseBill, SalesInvoice, PurchaseBillItem
from .serializers import PurchaseBillSerializer, SalesInvoiceSerializer
from inventory.serializers import ProductSerializer
from .filters import PurchaseBillFilter, SalesInvoiceFilter
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from decimal import Decimal
from django.db.models import Sum
from django.db import DatabaseError, ProgrammingError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
import json
import csv
import io


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('bill_number', openapi.IN_QUERY, description="Bill number", type=openapi.TYPE_STRING),
        openapi.Parameter('bill_date', openapi.IN_QUERY, description="Bill date (YYYY-MM-DD)", type=openapi.TYPE_STRING),
        openapi.Parameter('vendor_name', openapi.IN_QUERY, description="Vendor name", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="List of purchase bills",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "bill_number": "PB-001",
                    "bill_date": "2025-08-01",
                    "vendor_name": "Vendor A",
                    "total_amount": 10000,
                    "created_by": 1,
                    "created_at": "2025-08-01T10:00:00Z",
                    "items": [
                        {
                            "product": "uuid-prod-1",
                            "quantity": 10,
                            "unit": "pcs",
                            "price": 1000,
                            "amount": 10000
                        }
                    ]
                }
            ]
        }
    )}
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def purchase_bill_list_create(request):
    if request.method == 'GET':
        bills = PurchaseBill.objects.filter(created_by=request.user.active_tenant).order_by('-bill_date').prefetch_related('items__product')
        
        # Pagination
        try:
            page = int(request.GET.get('page', 1))
            limit = int(request.GET.get('limit', 10))
        except (TypeError, ValueError):
            return Response({
                "success": False,
                "message": "Validation error.",
                "errors": {"pagination": ["page and limit must be valid integers."]}
            }, status=status.HTTP_400_BAD_REQUEST)

        if page < 1 or limit < 1:
            return Response({
                "success": False,
                "message": "Validation error.",
                "errors": {"pagination": ["page and limit must be greater than 0."]}
            }, status=status.HTTP_400_BAD_REQUEST)
        
        total_count = bills.count()
        start = (page - 1) * limit
        end = start + limit
        
        paginated_bills = bills[start:end]
        serializer = PurchaseBillSerializer(paginated_bills, many=True)
        
        return Response({
            "success": True, 
            "data": serializer.data,
            "pagination": {
                "page": page,
                "limit": limit,
                "total_count": total_count,
                "total_pages": (total_count + limit - 1) // limit,
                "has_next": end < total_count,
                "has_prev": page > 1
            }
        })

    elif request.method == 'POST':
        serializer = PurchaseBillSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(created_by=request.user.active_tenant)
            return Response({
                "success": True,
                "message": "Purchase bill created successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response({
            "success": False,
            "message": "Validation error.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Purchase bill detail",
        examples={
            "application/json": {
                "id": "uuid-1",
                "bill_number": "PB-001",
                "bill_date": "2025-08-01",
                "vendor_name": "Vendor A",
                "total_amount": 10000,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-1",
                        "quantity": 10,
                        "unit": "pcs",
                        "price": 1000,
                        "amount": 10000
                    }
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def purchase_bill_detail(request, pk):
    try:
        bill = PurchaseBill.objects.get(pk=pk, created_by=request.user.active_tenant)
    except PurchaseBill.DoesNotExist:
        return Response({"success": False, "message": "Not found."}, status=404)
    serializer = PurchaseBillSerializer(bill)
    return Response({"success": True, "data": serializer.data})

@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('vendor_name', openapi.IN_QUERY, description="Vendor Name exactly as stored", type=openapi.TYPE_STRING, required=True),
    ],
    responses={200: "List of products"}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_products(request):
    vendor_name = request.GET.get('vendor_name', '').strip()
    if not vendor_name:
        return Response({"success": False, "message": "vendor_name is required."}, status=400)
    
    # Get distinct product IDs from PurchaseBillItems for this vendor
    product_ids = PurchaseBillItem.objects.filter(
        purchase_bill__vendor_name=vendor_name,
        purchase_bill__created_by=request.user.active_tenant
    ).values_list('product_id', flat=True).distinct()
    
    from inventory.models import Product
    products = Product.objects.filter(id__in=product_ids)
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['invoice_number', 'invoice_date', 'customer', 'items'],
        properties={
            'invoice_number': openapi.Schema(type=openapi.TYPE_STRING, description='Invoice number'),
            'invoice_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Invoice date (YYYY-MM-DD)'),
            'due_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description='Due date (YYYY-MM-DD)'),
            'customer': openapi.Schema(type=openapi.TYPE_STRING, description='Customer UUID'),
            'billing_address': openapi.Schema(type=openapi.TYPE_STRING, description='Billing address'),
            'shipping_address': openapi.Schema(type=openapi.TYPE_STRING, description='Shipping address'),
            'gst_treatment': openapi.Schema(type=openapi.TYPE_STRING, description='GST treatment type'),
            'journal': openapi.Schema(type=openapi.TYPE_STRING, description='Journal name'),
            'total_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Total amount'),
            'items': openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    required=['product', 'quantity', 'price', 'amount'],
                    properties={
                        'product': openapi.Schema(type=openapi.TYPE_STRING, description='Product UUID or name'),
                        'hsn_sac_code': openapi.Schema(type=openapi.TYPE_STRING, description='HSN/SAC code'),
                        'unit': openapi.Schema(type=openapi.TYPE_STRING, description='Unit of measurement'),
                        'quantity': openapi.Schema(type=openapi.TYPE_INTEGER, description='Quantity'),
                        'price': openapi.Schema(type=openapi.TYPE_NUMBER, description='Unit price'),
                        'amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Total amount'),
                        'discount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Discount amount'),
                        'tax': openapi.Schema(type=openapi.TYPE_NUMBER, description='Tax amount'),
                    }
                )
            )
        }
    ),
    responses={
        201: openapi.Response(
            description="Sales invoice created successfully",
            examples={
                "application/json": {
                    "id": "uuid-2",
                    "invoice_number": "INV-001",
                    "invoice_date": "2025-08-01",
                    "customer": "uuid-cust-1",
                    "total_amount": 15000,
                    "created_by": 1,
                    "created_at": "2025-08-01T11:00:00Z",
                    "items": [
                        {
                            "product": "uuid-prod-2",
                            "quantity": 5,
                            "unit": "pcs",
                            "price": 3000,
                            "amount": 15000,
                            "discount": 0,
                            "tax": 0
                        }
                    ]
                }
            }
        ),
        400: openapi.Response(
            description="Validation error",
            examples={
                "application/json": {
                    "errors": {
                        "invoice_number": ["This field is required."]
                    }
                }
            }
        )
    }
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sales_invoice_list_create(request):
    if request.method == 'GET':
        try:
            invoices = SalesInvoice.objects.filter(created_by=request.user.active_tenant).select_related('customer').prefetch_related('items__product').order_by('-invoice_date')

            customer_id = request.GET.get('customer')
            if customer_id:
                invoices = invoices.filter(customer_id=customer_id)

            status_filter = request.GET.get('status')
            if status_filter and status_filter != 'all':
                invoices = invoices.filter(status=status_filter)

            serializer = SalesInvoiceSerializer(invoices, many=True)
            return Response(serializer.data)
        except (ProgrammingError, DatabaseError) as exc:
            # Prevent raw 500s when schema is behind code (e.g., container not migrated yet).
            return Response({
                'error': 'Sales bills unavailable due to database schema mismatch.',
                'details': str(exc),
                'action': 'Run migrations in the backend container and retry.'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    elif request.method == 'POST':
        print("DEBUG: Sales invoice creation request received")
        print("DEBUG: Request data:", request.data)
        print("DEBUG: Request user:", request.user)
        
        # Check for invoice number collision
        invoice_number = request.data.get('invoice_number')
        if invoice_number and SalesInvoice.objects.filter(
            invoice_number=invoice_number, 
            created_by=request.user.active_tenant
        ).exists():
            return Response({
                'error': 'Invoice number already exists',
                'details': f"Invoice with number {invoice_number} already exists."
            }, status=status.HTTP_409_CONFLICT)
        
        serializer = SalesInvoiceSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            print("DEBUG: Serializer is valid, creating sales invoice")
            try:
                instance = serializer.save(created_by=request.user.active_tenant)
                print("DEBUG: Sales invoice created successfully:", instance.id)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                print("DEBUG: Error during save:", str(e))
                import traceback
                traceback.print_exc()
                return Response({
                    'error': 'Failed to create sales invoice',
                    'details': str(e)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            print("DEBUG: Serializer validation failed")
            try:
                error_details = serializer.errors
                print("DEBUG: Serializer errors:", error_details)
            except Exception as e:
                print("DEBUG: Error accessing serializer.errors:", str(e))
                error_details = {'general': ['Validation failed - unable to retrieve detailed errors']}
            
            return Response({
                'error': 'Validation failed',
                'details': error_details
            }, status=status.HTTP_400_BAD_REQUEST)
    

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Sales invoice detail",
        examples={
            "application/json": {
                "id": "uuid-2",
                "invoice_number": "INV-001",
                "invoice_date": "2025-08-01",
                "customer": "uuid-cust-1",
                "total_amount": 15000,
                "created_by": 1,
                "created_at": "2025-08-01T11:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-2",
                        "quantity": 5,
                        "unit": "pcs",
                        "price": 3000,
                        "amount": 15000
                    }
                ]
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_invoice_detail(request, pk):
    try:
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user.active_tenant)
    except SalesInvoice.DoesNotExist:
        return Response({"success": False, "message": "Not found."}, status=404)
    serializer = SalesInvoiceSerializer(invoice)
    return Response({"success": True, "data": serializer.data})

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=PurchaseBillSerializer,
    responses={200: openapi.Response(
        description="Updated purchase bill",
        examples={
            "application/json": {
                "id": "uuid-1",
                "bill_number": "PB-001",
                "bill_date": "2025-08-01",
                "vendor_name": "Vendor A",
                "total_amount": 10000,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-1",
                        "quantity": 10,
                        "unit": "pcs",
                        "price": 1000,
                        "amount": 10000
                    }
                ]
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def purchase_bill_update_delete(request, pk):
    try:
        bill = PurchaseBill.objects.get(pk=pk, created_by=request.user.active_tenant)
    except PurchaseBill.DoesNotExist:
        return Response({
            "success": False,
            "message": "Purchase bill not found."
        }, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = PurchaseBillSerializer(bill, data=request.data, partial=(request.method == 'PATCH'), context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Purchase bill updated successfully.",
                "data": serializer.data
            })
        return Response({
            "success": False,
            "message": "Validation error.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if bill.payment_status != 'pending':
            return Response({
                "success": False,
                "message": "Only pending purchase bills can be deleted."
            }, status=status.HTTP_400_BAD_REQUEST)
        bill.delete()
        return Response({
            "success": True,
            "message": "Purchase bill deleted successfully."
        }, status=status.HTTP_204_NO_CONTENT)

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=SalesInvoiceSerializer,
    responses={200: openapi.Response(
        description="Updated sales invoice",
        examples={
            "application/json": {
                "id": "uuid-2",
                "invoice_number": "INV-001",
                "invoice_date": "2025-08-01",
                "customer": "uuid-cust-1",
                "total_amount": 15000,
                "created_by": 1,
                "created_at": "2025-08-01T11:00:00Z",
                "items": [
                    {
                        "product": "uuid-prod-2",
                        "quantity": 5,
                        "unit": "pcs",
                        "price": 3000,
                        "amount": 15000
                    }
                ]
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_invoice_update_delete(request, pk):
    try:
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user.active_tenant)
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = SalesInvoiceSerializer(
            invoice, 
            data=request.data, 
            partial=(request.method == 'PATCH'),
            context={'request': request}  # Add this line
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if invoice.payment_status != 'pending':
            return Response({
                'error': 'Only pending sales invoices can be deleted.'
            }, status=status.HTTP_400_BAD_REQUEST)
        invoice.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_sales_invoices_csv(request):
    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant
    ).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')

    search = request.GET.get('search', '').strip()
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search)
            | Q(customer_name__icontains=search)
            | Q(customer__name__icontains=search)
        )

    status_filter = request.GET.get('status', '').strip()
    if status_filter and status_filter != 'all':
        invoices = invoices.filter(status=status_filter)

    customer_filter = request.GET.get('customer', '').strip()
    if customer_filter:
        invoices = invoices.filter(
            Q(customer_name__icontains=customer_filter)
            | Q(customer__name__icontains=customer_filter)
        )

    date_start = request.GET.get('date_start', '').strip() or request.GET.get('invoice_date_after', '').strip()
    date_end = request.GET.get('date_end', '').strip() or request.GET.get('invoice_date_before', '').strip()
    if date_start:
        invoices = invoices.filter(invoice_date__gte=date_start)
    if date_end:
        invoices = invoices.filter(invoice_date__lte=date_end)

    amount_min = request.GET.get('amount_min', '').strip()
    amount_max = request.GET.get('amount_max', '').strip()
    if amount_min:
        invoices = invoices.filter(total_amount__gte=amount_min)
    if amount_max:
        invoices = invoices.filter(total_amount__lte=amount_max)

    has_overdue = request.GET.get('has_overdue', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if has_overdue:
        today = timezone.localdate()
        invoices = invoices.filter(
            Q(due_date__lt=today) | (Q(due_date__isnull=True) & Q(invoice_date__lt=today))
        )

    selected_ids_param = request.GET.get('selected_ids', '').strip()
    if selected_ids_param:
        selected_ids = [value.strip() for value in selected_ids_param.split(',') if value.strip()]
        if selected_ids:
            invoices = invoices.filter(id__in=selected_ids)

    ordering = request.GET.get('ordering', '-invoice_date').strip()
    allowed_ordering = {
        'invoice_date', '-invoice_date',
        'created_at', '-created_at',
        'total_amount', '-total_amount',
        'invoice_number', '-invoice_number',
        'customer_name', '-customer_name',
    }
    if ordering not in allowed_ordering:
        ordering = '-invoice_date'
    invoices = invoices.order_by(ordering, '-created_at')

    invoice_data = SalesInvoiceSerializer(invoices, many=True).data

    headers = [
        'invoice_id',
        'invoice_number',
        'invoice_date',
        'due_date',
        'po_number',
        'po_date',
        'challan_number',
        'challan_date',
        'customer_id',
        'customer_name',
        'customer_email',
        'customer_phone',
        'customer_address',
        'customer_state',
        'customer_gstin',
        'customer_details_json',
        'delivery_address',
        'place_of_supply',
        'gst_treatment',
        'journal',
        'warehouse_id',
        'warehouse_name',
        'status',
        'total_amount',
        'amount_paid',
        'payment_status',
        'round_off',
        'created_by_id',
        'created_at',
        'meta_json',
        'items_count',
        'item_id',
        'product_id',
        'product_name',
        'product_description',
        'product_hsn_sac_code',
        'product_unit',
        'item_hsn_sac_code',
        'quantity',
        'free_quantity',
        'item_unit',
        'price',
        'discount',
        'tax',
        'amount',
        'batch_id',
        'batch_number',
        'batch_expiry_date',
        'batch_manufacturing_date',
        'batch_mrp',
        'batch_cost_price',
        'batch_sale_price',
        'batch_notes',
    ]

    def stringify(value):
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str, ensure_ascii=False)
        return value

    rows = []
    for invoice, serialized_invoice in zip(invoices, invoice_data):
        customer_details = serialized_invoice.get('customer_details') or {}
        items = list(invoice.items.all())
        serialized_items = serialized_invoice.get('items') or []
        if not items:
            items = [None]
            serialized_items = [None]

        for item, serialized_item in zip(items, serialized_items):
            product_detail = (serialized_item or {}).get('product_detail') if serialized_item else {}
            batch = getattr(item, 'batch', None) if item else None
            row = {
                'invoice_id': str(invoice.id),
                'invoice_number': invoice.invoice_number,
                'invoice_date': invoice.invoice_date,
                'due_date': invoice.due_date,
                'po_number': invoice.po_number,
                'po_date': invoice.po_date,
                'challan_number': invoice.challan_number,
                'challan_date': invoice.challan_date,
                'customer_id': str(getattr(invoice.customer, 'id', '') or ''),
                'customer_name': invoice.customer_name or customer_details.get('name') or getattr(invoice.customer, 'name', ''),
                'customer_email': serialized_invoice.get('customer_email') or customer_details.get('email') or getattr(invoice.customer, 'email', ''),
                'customer_phone': serialized_invoice.get('customer_phone') or customer_details.get('phone') or getattr(invoice.customer, 'phone', ''),
                'customer_address': serialized_invoice.get('customer_address') or customer_details.get('address') or getattr(invoice.customer, 'address', ''),
                'customer_state': customer_details.get('state') or getattr(invoice.customer, 'state', ''),
                'customer_gstin': customer_details.get('gstin') or getattr(invoice.customer, 'gstin', ''),
                'customer_details_json': customer_details,
                'delivery_address': invoice.delivery_address,
                'place_of_supply': invoice.place_of_supply,
                'gst_treatment': invoice.gst_treatment,
                'journal': invoice.journal,
                'warehouse_id': str(getattr(invoice.warehouse, 'id', '') or ''),
                'warehouse_name': getattr(invoice.warehouse, 'name', ''),
                'status': invoice.status,
                'total_amount': invoice.total_amount,
                'amount_paid': invoice.amount_paid,
                'payment_status': invoice.payment_status,
                'round_off': invoice.round_off,
                'created_by_id': str(getattr(invoice.created_by, 'id', '') or ''),
                'created_at': invoice.created_at,
                'meta_json': serialized_invoice.get('meta'),
                'items_count': len(items) if item is not None else 0,
                'item_id': str(getattr(item, 'id', '') or '') if item else '',
                'product_id': str(getattr(getattr(item, 'product', None), 'id', '') or '') if item else '',
                'product_name': product_detail.get('name') if product_detail else '',
                'product_description': product_detail.get('description') if product_detail else '',
                'product_hsn_sac_code': product_detail.get('hsn_sac_code') if product_detail else '',
                'product_unit': product_detail.get('unit') if product_detail else '',
                'item_hsn_sac_code': getattr(item, 'hsn_sac_code', '') if item else '',
                'quantity': getattr(item, 'quantity', '') if item else '',
                'free_quantity': getattr(item, 'free_quantity', '') if item else '',
                'item_unit': getattr(item, 'unit', '') if item else '',
                'price': getattr(item, 'price', '') if item else '',
                'discount': getattr(item, 'discount', '') if item else '',
                'tax': getattr(item, 'tax', '') if item else '',
                'amount': getattr(item, 'amount', '') if item else '',
                'batch_id': str(getattr(batch, 'id', '') or '') if batch else '',
                'batch_number': getattr(batch, 'batch_number', '') if batch else '',
                'batch_expiry_date': getattr(batch, 'expiry_date', '') if batch else '',
                'batch_manufacturing_date': getattr(batch, 'manufacturing_date', '') if batch else '',
                'batch_mrp': getattr(batch, 'mrp', '') if batch else '',
                'batch_cost_price': getattr(batch, 'cost_price', '') if batch else '',
                'batch_sale_price': getattr(batch, 'sale_price', '') if batch else '',
                'batch_notes': getattr(batch, 'notes', '') if batch else '',
            }
            rows.append({key: stringify(value) for key, value in row.items()})

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

    response = HttpResponse(buffer.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="sales-invoices-{timezone.localdate()}.csv"'
    return response

from django.db.models import Sum, Count
from django.utils import timezone
import re

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_next_invoice_number(request):
    prefix = request.GET.get('prefix', 'INV-')
    tenant_id = str(request.user.active_tenant.id)[:4].upper()
    full_prefix = f"{prefix}{tenant_id}-"
    
    # Find max invoice number with this prefix
    invoices = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant,
        invoice_number__startswith=full_prefix
    )
    
    max_num = 0
    for inv in invoices:
        suffix = inv.invoice_number.replace(full_prefix, '')
        try:
            num = int(suffix)
            if num > max_num:
                max_num = num
        except ValueError:
            pass
            
    next_num = max_num + 1
    next_invoice = f"{full_prefix}{next_num:03d}"
    return Response({
        "success": True,
        "uuid_prefix": tenant_id,
        "next_number": next_invoice,
        "suffix": f"{next_num:03d}"
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_summary_analytics(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    
    qs = SalesInvoice.objects.filter(created_by=request.user.active_tenant)
    if start_date:
        qs = qs.filter(invoice_date__gte=start_date)
    if end_date:
        qs = qs.filter(invoice_date__lte=end_date)
        
    total_revenue = qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    total_invoices = qs.count()
    
    now = timezone.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    this_month_qs = SalesInvoice.objects.filter(
        created_by=request.user.active_tenant,
        invoice_date__gte=this_month_start.date()
    )
    this_month_revenue = this_month_qs.aggregate(Sum('total_amount'))['total_amount__sum'] or 0
    this_month_invoices = this_month_qs.count()
    
    return Response({
        "success": True,
        "total_revenue": total_revenue,
        "total_invoices": total_invoices,
        "this_month_revenue": this_month_revenue,
        "this_month_invoices": this_month_invoices,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recalculate_invoice_totals(request):
    """
    Recalculate invoice totals from items for all invoices (for fixing data inconsistencies).
    """
    invoices = SalesInvoice.objects.filter(created_by=request.user.active_tenant)
    fixed_count = 0
    
    for invoice in invoices:
        items_total = sum(
            Decimal(str(item.amount or 0)) 
            for item in invoice.items.all()
        )
        
        if invoice.total_amount != items_total:
            invoice.total_amount = items_total
            invoice.save(update_fields=['total_amount'])
            fixed_count += 1
    
    return Response({
        "success": True,
        "message": f"Fixed {fixed_count} invoices with incorrect totals"
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_sales_invoices_csv(request):
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return Response({'error': 'CSV file is required using form key "file".'}, status=status.HTTP_400_BAD_REQUEST)

    if not uploaded_file.name.lower().endswith('.csv'):
        return Response({'error': 'Only CSV files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        raw_content = uploaded_file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return Response({'error': 'Unable to decode CSV. Please upload a UTF-8 encoded file.'}, status=status.HTTP_400_BAD_REQUEST)

    reader = csv.DictReader(io.StringIO(raw_content))
    required_headers = {'bill_number', 'sale_date', 'customer_name', 'product_name', 'quantity', 'price'}
    if not reader.fieldnames or not required_headers.issubset(set(h.strip() for h in reader.fieldnames if h)):
        return Response({
            'error': 'Invalid CSV template.',
            'details': 'Required columns: bill_number, sale_date, customer_name, product_name, quantity, price.'
        }, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    created_count = 0

    from inventory.models import Product
    from decimal import Decimal
    from .models import Customer, SalesInvoice, SalesInvoiceItem

    with transaction.atomic():
        for row in reader:
            if not any((value or '').strip() for value in row.values()):
                continue

            bill_number = (row.get('bill_number') or '').strip()
            sale_date = (row.get('sale_date') or '').strip()
            customer_name = (row.get('customer_name') or '').strip()
            customer_address = (row.get('customer_address') or '').strip()
            customer_gstin = (row.get('customer_gstin') or '').strip()
            due_date = (row.get('due_date') or '').strip() or None
            product_name = (row.get('product_name') or '').strip()
            quantity = int(float(row.get('quantity') or 0))
            unit = (row.get('unit') or 'pcs').strip() or 'pcs'
            price = Decimal(str(row.get('price') or '0'))
            discount = Decimal(str(row.get('discount') or '0'))
            tax = Decimal(str(row.get('tax') or '0'))
            hsn_code = (row.get('hsn_code') or '').strip()

            if not (bill_number and sale_date and customer_name and product_name and quantity > 0):
                raise ValueError('Each row must include bill_number, sale_date, customer_name, product_name, and a quantity greater than 0.')

            customer, _ = Customer.objects.get_or_create(
                created_by=tenant,
                name=customer_name,
                defaults={
                    'address': customer_address or None,
                    'gstin': customer_gstin or None,
                }
            )
            if customer_address and customer.address != customer_address:
                customer.address = customer_address
            if customer_gstin and customer.gstin != customer_gstin:
                customer.gstin = customer_gstin
            customer.save()

            product, _ = Product.objects.get_or_create(
                created_by=tenant,
                name=product_name,
                defaults={
                    'unit': unit,
                    'hsn_sac_code': hsn_code or None,
                    'price': price,
                    'tax': tax,
                }
            )

            if SalesInvoice.objects.filter(created_by=tenant, invoice_number=bill_number).exists():
                continue

            invoice = SalesInvoice.objects.create(
                created_by=tenant,
                customer=customer,
                customer_name=customer_name,
                customer_address=customer_address or customer.address,
                invoice_number=bill_number,
                invoice_date=sale_date,
                due_date=due_date,
                total_amount=Decimal('0'),
                journal='Sales',
                status='final',
            )

            base_amount = Decimal(str(quantity)) * price
            discount_amount = (base_amount * discount) / Decimal('100')
            taxable_amount = base_amount - discount_amount
            tax_amount = (taxable_amount * tax) / Decimal('100')
            amount = (taxable_amount + tax_amount).quantize(Decimal('0.01'))

            SalesInvoiceItem.objects.create(
                sales_invoice=invoice,
                product=product,
                quantity=quantity,
                price=price,
                discount=discount,
                tax=tax,
                amount=amount,
                unit=unit,
                hsn_sac_code=hsn_code or None,
            )

            invoice.total_amount = amount
            invoice.save(update_fields=['total_amount'])
            created_count += 1

    return Response({
        'success': True,
        'created_count': created_count,
        'message': f'Successfully imported {created_count} sales invoices.'
    }, status=status.HTTP_201_CREATED)

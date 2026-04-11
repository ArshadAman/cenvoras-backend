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
from django.db import DatabaseError, ProgrammingError
from django.utils import timezone


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

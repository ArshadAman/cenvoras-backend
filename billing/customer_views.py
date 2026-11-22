from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .serializers import CustomerSerializer
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db import models
from .models import Customer
from .balance_sync import recompute_customer_balances_for_customers
from cenvoras.pagination import StandardResultsSetPagination


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by name or email", type=openapi.TYPE_STRING),
        openapi.Parameter('ordering', openapi.IN_QUERY, description="Order by field (e.g., 'name', '-created_at')", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="List of customers",
        examples={
            "application/json": [
                {
                    "id": "uuid-1",
                    "name": "John Doe",
                    "email": "john@example.com",
                    "phone": "+1-555-123-4567",
                    "gstin": "GST123456789",
                    "address": "123 Main St, City, State",
                    "created_by": 1,
                    "created_at": "2025-09-27T10:00:00Z"
                }
            ]
        }
    )}
)
@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['name'],
        properties={
            'name': openapi.Schema(type=openapi.TYPE_STRING, description='Customer name'),
            'email': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_EMAIL, description='Customer email'),
            'phone': openapi.Schema(type=openapi.TYPE_STRING, description='Customer phone number'),
            'gstin': openapi.Schema(type=openapi.TYPE_STRING, description='Customer GSTIN'),
            'address': openapi.Schema(type=openapi.TYPE_STRING, description='Customer address'),
        }
    ),
    responses={
        201: openapi.Response(
            description="Customer created successfully",
            examples={
                "application/json": {
                    "success": True,
                    "message": "Customer created successfully.",
                    "data": {
                        "id": "uuid-1",
                        "name": "John Doe",
                        "email": "john@example.com",
                        "phone": "+1-555-123-4567",
                        "gstin": "GST123456789",
                        "address": "123 Main St, City, State",
                        "created_by": 1,
                        "created_at": "2025-09-27T10:00:00Z"
                    }
                }
            }
        ),
        400: openapi.Response(
            description="Validation error",
            examples={
                "application/json": {
                    "success": False,
                    "message": "Validation error.",
                    "errors": {
                        "name": ["This field is required."]
                    }
                }
            }
        )
    }
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def customer_list_create(request):
    tenant = getattr(request.user, 'active_tenant', request.user)

    if request.method == 'GET':
        # Get query parameters
        search = request.query_params.get('search', '')
        ordering = request.query_params.get('ordering', '-created_at')
        
        # Filter customers for the authenticated user
        customers = Customer.objects.filter(created_by=tenant).select_related('linked_vendor')
        
        # Apply search filter
        if search:
            customers = customers.filter(
                models.Q(name__icontains=search) | 
                models.Q(email__icontains=search)
            )
        
        # Apply ordering
        if ordering:
            customers = customers.order_by(ordering)
        
        if request.query_params.get('all', '').lower() in ['1', 'true', 'yes']:
            customers_list = list(customers)
            recompute_customer_balances_for_customers(customers_list, tenant)
            serializer = CustomerSerializer(customers_list, many=True)
            return Response(serializer.data)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(customers, request)
        if page is not None:
            recompute_customer_balances_for_customers(page, tenant)
            serializer = CustomerSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        customers_list = list(customers)
        recompute_customer_balances_for_customers(customers_list, tenant)
        serializer = CustomerSerializer(customers_list, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = CustomerSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Customer created successfully.",
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
        description="Customer details",
        examples={
            "application/json": {
                "id": "uuid-1",
                "name": "John Doe",
                "email": "john@example.com",
                "phone": "+1-555-123-4567",
                "gstin": "GST123456789",
                "address": "123 Main St, City, State",
                "created_by": 1,
                "created_at": "2025-09-27T10:00:00Z"
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def customer_detail(request, pk):
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        from django.db.models import Q
        customer = Customer.objects.get(Q(created_by=tenant) | Q(created_by__parent=tenant), pk=pk)
    except Customer.DoesNotExist:
        return Response({
            "success": False,
            "message": "Customer not found."
        }, status=status.HTTP_404_NOT_FOUND)

    recompute_customer_balances_for_customers([customer], tenant)
    customer.refresh_from_db()
    
    serializer = CustomerSerializer(customer)
    return Response(serializer.data)

@swagger_auto_schema(
    method='put',
    request_body=CustomerSerializer,
    responses={200: openapi.Response(
        description="Customer updated successfully",
        examples={
            "application/json": {
                "success": True,
                "message": "Customer updated successfully.",
                "data": {
                    "id": "uuid-1",
                    "name": "John Doe Updated",
                    "email": "john.updated@example.com",
                    "phone": "+1-555-123-4567",
                    "gstin": "GST123456789",
                    "address": "456 Updated St, City, State",
                    "created_by": 1,
                    "created_at": "2025-09-27T10:00:00Z"
                }
            }
        }
    )}
)
@swagger_auto_schema(
    method='patch',
    request_body=CustomerSerializer,
    responses={200: openapi.Response(
        description="Customer partially updated successfully"
    )}
)
@swagger_auto_schema(
    method='delete',
    responses={204: openapi.Response(
        description="Customer deleted successfully"
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def customer_update_delete(request, pk):
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        from django.db.models import Q
        customer = Customer.objects.get(Q(created_by=tenant) | Q(created_by__parent=tenant), pk=pk)
    except Customer.DoesNotExist:
        return Response({
            "success": False,
            "message": "Customer not found."
        }, status=status.HTTP_404_NOT_FOUND)
    
    if request.method in ['PUT', 'PATCH']:
        serializer = CustomerSerializer(
            customer, 
            data=request.data, 
            partial=(request.method == 'PATCH'),
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Customer updated successfully.",
                "data": serializer.data
            })
        return Response({
            "success": False,
            "message": "Validation error.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'DELETE':
        from django.db.models import ProtectedError
        try:
            customer.delete()
            return Response({
                "success": True,
                "message": "Customer deleted successfully."
            }, status=status.HTTP_204_NO_CONTENT)
        except ProtectedError as e:
            docs = []
            doc_summary = []
            for obj in getattr(e, 'protected_objects', []):
                model_name = getattr(obj._meta, 'verbose_name', type(obj).__name__).title()
                obj_cls = type(obj).__name__

                num = (
                    getattr(obj, 'invoice_number', None) or 
                    getattr(obj, 'order_number', None) or 
                    getattr(obj, 'quotation_number', None) or 
                    getattr(obj, 'challan_number', None) or 
                    getattr(obj, 'credit_note_number', None) or 
                    getattr(obj, 'reference', None) or
                    getattr(obj, 'receipt_voucher_number', None)
                )
                if not num and obj_cls == 'AdvanceAdjustment':
                    payment_ref = getattr(getattr(obj, 'payment', None), 'reference', '')
                    num = f"Advance Adjustment ({payment_ref or getattr(obj, 'date', '')})"
                elif not num:
                    num = str(obj)

                raw_status = getattr(obj, 'status', getattr(obj, 'stage', None))

                module_route = '/sales'
                if obj_cls == 'SalesOrder':
                    module_route = '/sales-orders'
                elif obj_cls == 'Quotation':
                    module_route = '/quotations'
                elif obj_cls == 'DeliveryChallan':
                    module_route = '/delivery-challans'
                elif obj_cls == 'CreditNote':
                    module_route = '/credit-notes'
                elif obj_cls in ('Payment', 'AdvanceAdjustment'):
                    module_route = '/payments'

                docs.append({
                    'type': model_name,
                    'identifier': str(num),
                    'id': str(getattr(obj, 'id', '')),
                    'status': str(raw_status) if raw_status else None,
                    'route': module_route,
                })
                status_suffix = f" ({raw_status})" if raw_status else ""
                doc_summary.append(f"{model_name} '{num}'{status_suffix}")

            if doc_summary:
                detail_msg = ", ".join(doc_summary[:5])
                if len(doc_summary) > 5:
                    detail_msg += f" and {len(doc_summary) - 5} more"
                msg = f"Cannot delete customer '{customer.name}'. Linked to: {detail_msg}. Delete or reassign these documents first."
            else:
                msg = f"Cannot delete customer '{customer.name}'. They are currently linked to existing transactions (invoices, orders, quotations, etc.). Delete those transactions first."

            return Response({
                "success": False,
                "message": msg,
                "blocking_documents": docs
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                "success": False,
                "message": "Failed to delete customer. Please try again later."
            }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def customer_ensure_vendor(request, pk):
    """Create or reuse the vendor counterpart so a customer can be billed as a vendor."""
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        customer = Customer.objects.get(pk=pk, created_by=tenant)
    except Customer.DoesNotExist:
        return Response({"success": False, "message": "Customer not found."}, status=status.HTTP_404_NOT_FOUND)

    from billing.party_link import ensure_vendor_for_customer
    from .serializers import VendorSerializer
    vendor = ensure_vendor_for_customer(customer, tenant)
    return Response({
        "success": True,
        "vendor": VendorSerializer(vendor).data,
        "customer_id": str(customer.id),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def customer_duplicates(request):
    """List duplicate customer clusters (matching GSTIN or name) for current tenant."""
    tenant = getattr(request.user, 'active_tenant', request.user)
    from .services_customer_merge import find_duplicate_customers
    clusters = find_duplicate_customers(tenant)
    return Response({
        "success": True,
        "count": len(clusters),
        "clusters": clusters
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def customer_merge(request):
    """
    Merge duplicate customer into primary customer.
    Body: { "primary_id": "...", "duplicate_id": "..." }
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    primary_id = request.data.get('primary_id') or request.data.get('primary_customer_id')
    duplicate_id = request.data.get('duplicate_id') or request.data.get('duplicate_customer_id')

    if not primary_id or not duplicate_id:
        return Response({
            "success": False,
            "message": "Both primary_id and duplicate_id are required."
        }, status=status.HTTP_400_BAD_REQUEST)

    from .services_customer_merge import merge_customers
    try:
        result = merge_customers(tenant, primary_id, duplicate_id, actor=request.user)
        return Response({
            "success": True,
            "message": "Customers merged successfully.",
            "data": result
        }, status=status.HTTP_200_OK)
    except ValueError as e:
        return Response({
            "success": False,
            "message": str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            "success": False,
            "message": f"An error occurred while merging customers: {str(e)}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

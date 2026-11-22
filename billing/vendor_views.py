from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .serializers import VendorSerializer
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db import models
from .models import Vendor
from cenvoras.pagination import StandardResultsSetPagination


@swagger_auto_schema(
    method='get',
    manual_parameters=[
        openapi.Parameter('search', openapi.IN_QUERY, description="Search by name or email", type=openapi.TYPE_STRING),
        openapi.Parameter('ordering', openapi.IN_QUERY, description="Order by field (e.g., 'name', '-created_at')", type=openapi.TYPE_STRING),
    ],
    responses={200: openapi.Response(
        description="List of vendors",
    )}
)
@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['name'],
        properties={
            'name': openapi.Schema(type=openapi.TYPE_STRING, description='Vendor name'),
            'email': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_EMAIL, description='Vendor email'),
            'phone': openapi.Schema(type=openapi.TYPE_STRING, description='Vendor phone number'),
            'gstin': openapi.Schema(type=openapi.TYPE_STRING, description='Vendor GSTIN'),
            'address': openapi.Schema(type=openapi.TYPE_STRING, description='Vendor address'),
        }
    ),
    responses={
        201: openapi.Response(description="Vendor created successfully"),
        400: openapi.Response(description="Validation error")
    }
)
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def vendor_list_create(request):
    tenant = getattr(request.user, 'active_tenant', request.user)
    if request.method == 'GET':
        # Get query parameters
        search = request.query_params.get('search', '')
        ordering = request.query_params.get('ordering', '-created_at')
        
        # Filter vendors for the authenticated user
        vendors = Vendor.objects.filter(created_by=tenant)
        
        # Apply search filter
        if search:
            vendors = vendors.filter(
                models.Q(name__icontains=search) | 
                models.Q(email__icontains=search)
            )
        
        # Apply ordering
        if ordering:
            vendors = vendors.order_by(ordering)
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(vendors, request)
        if page is not None:
            serializer = VendorSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = VendorSerializer(vendors, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = VendorSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Vendor created successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response({
            "success": False,
            "message": "Validation error.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(description="Vendor details")}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_detail(request, pk):
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        vendor = Vendor.objects.get(pk=pk, created_by=tenant)
    except Vendor.DoesNotExist:
        return Response({
            "success": False,
            "message": "Vendor not found."
        }, status=status.HTTP_404_NOT_FOUND)
    
    serializer = VendorSerializer(vendor)
    return Response(serializer.data)

@swagger_auto_schema(
    method='put',
    request_body=VendorSerializer,
    responses={200: openapi.Response(description="Vendor updated successfully")}
)
@swagger_auto_schema(
    method='patch',
    request_body=VendorSerializer,
    responses={200: openapi.Response(description="Vendor partially updated successfully")}
)
@swagger_auto_schema(
    method='delete',
    responses={204: openapi.Response(description="Vendor deleted successfully")}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def vendor_update_delete(request, pk):
    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        vendor = Vendor.objects.get(pk=pk, created_by=tenant)
    except Vendor.DoesNotExist:
        return Response({
            "success": False,
            "message": "Vendor not found."
        }, status=status.HTTP_404_NOT_FOUND)
    
    if request.method in ['PUT', 'PATCH']:
        serializer = VendorSerializer(
            vendor, 
            data=request.data, 
            partial=(request.method == 'PATCH'),
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Vendor updated successfully.",
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
            vendor.delete()
            return Response({
                "success": True,
                "message": "Vendor deleted successfully."
            }, status=status.HTTP_204_NO_CONTENT)
        except ProtectedError as e:
            docs = []
            doc_summary = []
            for obj in getattr(e, 'protected_objects', []):
                model_name = getattr(obj._meta, 'verbose_name', type(obj).__name__).title()
                num = (
                    getattr(obj, 'bill_number', None) or 
                    getattr(obj, 'po_number', None) or 
                    getattr(obj, 'order_number', None) or 
                    getattr(obj, 'debit_note_number', None) or 
                    str(obj)
                )
                docs.append({'type': model_name, 'identifier': str(num), 'id': str(getattr(obj, 'id', ''))})
                doc_summary.append(f"{model_name} '{num}'")

            if doc_summary:
                detail_msg = ", ".join(doc_summary[:5])
                if len(doc_summary) > 5:
                    detail_msg += f" and {len(doc_summary) - 5} more"
                msg = f"Cannot delete vendor '{vendor.name}'. Linked to: {detail_msg}. Delete or reassign these documents first."
            else:
                msg = f"Cannot delete vendor '{vendor.name}'. They are currently linked to existing transactions (purchase bills, purchase orders, etc.). Delete those transactions first."

            return Response({
                "success": False,
                "message": msg,
                "blocking_documents": docs
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                "success": False,
                "message": "Failed to delete vendor. Please try again later."
            }, status=status.HTTP_400_BAD_REQUEST)

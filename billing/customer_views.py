from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .serializers import CustomerSerializer
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Customer


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
    if request.method == 'GET':
        # Get query parameters
        search = request.query_params.get('search', '')
        ordering = request.query_params.get('ordering', '-created_at')
        
        # Filter customers for the authenticated user
        customers = Customer.objects.filter(created_by=request.user)
        
        # Apply search filter
        if search:
            customers = customers.filter(
                models.Q(name__icontains=search) | 
                models.Q(email__icontains=search)
            )
        
        # Apply ordering
        if ordering:
            customers = customers.order_by(ordering)
        
        serializer = CustomerSerializer(customers, many=True)
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
    try:
        customer = Customer.objects.get(pk=pk, created_by=request.user)
    except Customer.DoesNotExist:
        return Response({
            "success": False,
            "message": "Customer not found."
        }, status=status.HTTP_404_NOT_FOUND)
    
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
    try:
        customer = Customer.objects.get(pk=pk, created_by=request.user)
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
        # Check if customer has any related invoices before deletion
        if customer.salesinvoice_set.exists():
            return Response({
                "success": False,
                "message": "Cannot delete customer with existing sales invoices."
            }, status=status.HTTP_400_BAD_REQUEST)
        
        customer.delete()
        return Response({
            "success": True,
            "message": "Customer deleted successfully."
        }, status=status.HTTP_204_NO_CONTENT)
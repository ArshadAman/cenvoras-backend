from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import PurchaseBill, SalesInvoice
from .serializers import PurchaseBillSerializer, SalesInvoiceSerializer
from .filters import PurchaseBillFilter, SalesInvoiceFilter
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


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
        bills = PurchaseBill.objects.filter(created_by=request.user).order_by('-bill_date')
        serializer = PurchaseBillSerializer(bills, many=True)
        return Response({"success": True, "data": serializer.data})

    elif request.method == 'POST':
        serializer = PurchaseBillSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(created_by=request.user)
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
        bill = PurchaseBill.objects.get(pk=pk, created_by=request.user)
    except PurchaseBill.DoesNotExist:
        return Response({"success": False, "message": "Not found."}, status=404)
    serializer = PurchaseBillSerializer(bill)
    return Response({"success": True, "data": serializer.data})


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
        invoices = SalesInvoice.objects.filter(created_by=request.user).order_by('-invoice_date')
        serializer = SalesInvoiceSerializer(invoices, many=True)
        return Response(serializer.data)
    elif request.method == 'POST':
        print("DEBUG: Sales invoice creation request received")
        print("DEBUG: Request data:", request.data)
        print("DEBUG: Request user:", request.user)
        
        serializer = SalesInvoiceSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            print("DEBUG: Serializer is valid, creating sales invoice")
            try:
                instance = serializer.save(created_by=request.user)
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
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user)
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
        bill = PurchaseBill.objects.get(pk=pk, created_by=request.user)
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
        invoice = SalesInvoice.objects.get(pk=pk, created_by=request.user)
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
        invoice.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

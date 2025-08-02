from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import Product
from .serializers import ProductSerializer
import csv
from io import TextIOWrapper
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.parsers import MultiPartParser

# Create your views here.

@swagger_auto_schema(
    method='post',
    request_body=ProductSerializer,
    responses={201: openapi.Response(
        description="Product created",
        examples={
            "application/json": {
                "id": "uuid-1",
                "name": "Product A",
                "hsn_code": "1234",
                "stock": 50,
                "unit": "pcs",
                "purchase_price": 100,
                "low_stock_alert": 10,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z"
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_product(request):
    serializer = ProductSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(created_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='post',
    manual_parameters=[
        openapi.Parameter(
            'file', openapi.IN_FORM, description="CSV file", type=openapi.TYPE_FILE, required=True
        ),
    ],
    responses={200: openapi.Response(
        description="Bulk product upload result",
        examples={
            "application/json": {
                "created": [
                    {
                        "id": "uuid-1",
                        "name": "Product A",
                        "hsn_code": "1234",
                        "stock": 50,
                        "unit": "pcs",
                        "purchase_price": 100,
                        "low_stock_alert": 10,
                        "created_by": 1
                    }
                ],
                "errors": []
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_products_csv(request):
    """
    Expects a CSV file with columns: name, hsn_code, stock, unit, purchase_price, low_stock_alert
    """
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)
    reader = csv.DictReader(TextIOWrapper(file, encoding='utf-8'))
    created = []
    errors = []
    for row in reader:
        serializer = ProductSerializer(data=row)
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            created.append(serializer.data)
        else:
            errors.append({'row': row, 'errors': serializer.errors})
    return Response({'created': created, 'errors': errors})

@swagger_auto_schema(
    methods=['put', 'patch', 'delete'],
    request_body=ProductSerializer,
    responses={200: openapi.Response(
        description="Product updated",
        examples={
            "application/json": {
                "id": "uuid-1",
                "name": "Product A",
                "hsn_code": "1234",
                "stock": 60,
                "unit": "pcs",
                "purchase_price": 100,
                "low_stock_alert": 10,
                "created_by": 1,
                "created_at": "2025-08-01T10:00:00Z"
            }
        }
    )}
)
@api_view(['PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def product_update_delete(request, pk):
    try:
        product = Product.objects.get(pk=pk, created_by=request.user)
    except Product.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method in ['PUT', 'PATCH']:
        serializer = ProductSerializer(product, data=request.data, partial=(request.method == 'PATCH'))
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

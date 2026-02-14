from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Product, Warehouse, StockPoint, StockTransfer, ProductBatch
from .serializers import (
    ProductSerializer, WarehouseSerializer, StockPointSerializer, 
    StockTransferSerializer, ProductBatchSerializer
)

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class WarehouseListCreateView(generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def stock_point_list(request):
    """
    List stock points (inventory levels per batch per warehouse)
    Optional filters: ?product=UUID & ?warehouse=UUID
    """
    queryset = StockPoint.objects.all()
    
    # Filter by user's warehouses
    user_warehouses = Warehouse.objects.filter(created_by=request.user)
    queryset = queryset.filter(warehouse__in=user_warehouses)
    
    product_id = request.query_params.get('product')
    if product_id:
        queryset = queryset.filter(batch__product__id=product_id)
        
    warehouse_id = request.query_params.get('warehouse')
    if warehouse_id:
        queryset = queryset.filter(warehouse__id=warehouse_id)
        
    serializer = StockPointSerializer(queryset, many=True)
    return Response(serializer.data)

class StockTransferListCreateView(generics.ListCreateAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockTransfer.objects.filter(created_by=self.request.user).order_by('-transfer_date')

class StockTransferDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockTransfer.objects.filter(created_by=self.request.user)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def batch_list(request):
    """
    List product batches.
    Optional filters: ?product=UUID
    """
    queryset = ProductBatch.objects.filter(product__created_by=request.user)
    
    product_id = request.query_params.get('product')
    if product_id:
        queryset = queryset.filter(product__id=product_id)
        
    serializer = ProductBatchSerializer(queryset, many=True)
    return Response(serializer.data)

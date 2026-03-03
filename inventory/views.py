from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, F
from .models import Product, Warehouse, StockPoint, StockTransfer, ProductBatch
from .models_pricing import PriceList, Scheme
from .serializers import (
    ProductSerializer, WarehouseSerializer, StockPointSerializer, 
    StockTransferSerializer, ProductBatchSerializer,
    PriceListSerializer, SchemeSerializer

)

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

class ProductBatchListView(generics.ListAPIView):
    serializer_class = ProductBatchSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Corrected `request.user` to `self.request.user` for class-based view
        # Removed `perform_create` as it's a ListAPIView
        return ProductBatch.objects.select_related('product').filter(product__created_by=self.request.user)

class WarehouseListCreateView(generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# Feature 2: Multi-Store / Godown — Detail/Update/Delete
class WarehouseDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(created_by=self.request.user)


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

class StockPointListView(generics.ListAPIView):
    # Assuming StockPointDetailSerializer is intended, otherwise StockPointSerializer
    serializer_class = StockPointSerializer # Changed to StockPointSerializer as StockPointDetailSerializer is not defined
    permission_classes = [permissions.IsAuthenticated] # Corrected IsAuthenticated import

    def get_queryset(self):
        # Corrected the incomplete line and added user filtering
        user_warehouses = Warehouse.objects.filter(created_by=self.request.user)
        return StockPoint.objects.select_related('batch', 'warehouse', 'batch__product').filter(warehouse__in=user_warehouses)

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


# =============================================================================
# Feature 19: Expiry Stock Report
# =============================================================================
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def expiry_report(request):
    """
    Batches expiring within N days (default 90).
    ?days=90 — configurable window.
    Returns batches with stock > 0 that expire within the window.
    """
    days = int(request.query_params.get('days', 90))
    today = timezone.now().date()
    cutoff = today + timedelta(days=days)

    batches = ProductBatch.objects.filter(
        product__created_by=request.user,
        expiry_date__isnull=False,
        expiry_date__lte=cutoff,
        is_active=True,
    ).select_related('product').order_by('expiry_date')

    results = []
    for batch in batches:
        total_qty = batch.stock_points.aggregate(total=Sum('quantity'))['total'] or 0
        if total_qty <= 0:
            continue
        days_left = (batch.expiry_date - today).days
        results.append({
            'batch_id': str(batch.id),
            'product_name': batch.product.name,
            'batch_number': batch.batch_number,
            'expiry_date': batch.expiry_date,
            'days_until_expiry': days_left,
            'status': 'expired' if days_left < 0 else ('critical' if days_left <= 30 else 'warning'),
            'quantity': total_qty,
            'mrp': float(batch.mrp),
            'value': float(batch.mrp * total_qty),
        })

    return Response({
        'count': len(results),
        'days_window': days,
        'total_value_at_risk': sum(r['value'] for r in results),
        'results': results,
    })


# =============================================================================
# Feature 27: Shortage Management Report
# =============================================================================
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def shortage_report(request):
    """
    Products where current stock < low_stock_alert threshold.
    """
    products = Product.objects.filter(
        created_by=request.user,
        low_stock_alert__gt=0,
    ).order_by('stock')

    results = []
    for p in products:
        if p.stock < p.low_stock_alert:
            deficit = p.low_stock_alert - p.stock
            results.append({
                'product_id': str(p.id),
                'product_name': p.name,
                'current_stock': p.stock,
                'alert_threshold': p.low_stock_alert,
                'deficit': deficit,
                'unit': p.unit,
                'severity': 'critical' if p.stock == 0 else ('high' if deficit > p.low_stock_alert * 0.5 else 'medium'),
            })

    return Response({
        'count': len(results),
        'critical_count': sum(1 for r in results if r['severity'] == 'critical'),
        'results': results,
    })


# =============================================================================
# Feature 25: Batch Split
# =============================================================================
@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def batch_split(request):
    """
    Split a batch into two.
    Body: { "batch_id": "uuid", "new_batch_number": "B001-1", "split_quantity": 50 }
    Moves split_quantity from original batch to a new batch in same warehouses.
    """
    batch_id = request.data.get('batch_id')
    new_batch_number = request.data.get('new_batch_number', '').strip()
    split_qty = int(request.data.get('split_quantity', 0))

    if not batch_id or not new_batch_number or split_qty <= 0:
        return Response({'error': 'batch_id, new_batch_number, and split_quantity (>0) are required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        original = ProductBatch.objects.get(id=batch_id, product__created_by=request.user)
    except ProductBatch.DoesNotExist:
        return Response({'error': 'Batch not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Check total stock in original batch
    total_stock = original.stock_points.aggregate(total=Sum('quantity'))['total'] or 0
    if split_qty >= total_stock:
        return Response({'error': f'Split quantity ({split_qty}) must be less than total stock ({total_stock}).'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Create new batch
    new_batch = ProductBatch.objects.create(
        product=original.product,
        batch_number=new_batch_number,
        expiry_date=original.expiry_date,
        manufacturing_date=original.manufacturing_date,
        mrp=original.mrp,
        cost_price=original.cost_price,
        sale_price=original.sale_price,
    )

    # Distribute: take split_qty from stock points (first-come)
    remaining = split_qty
    for sp in original.stock_points.filter(quantity__gt=0).order_by('-quantity'):
        if remaining <= 0:
            break
        take = min(sp.quantity, remaining)
        sp.quantity -= take
        sp.save()
        # Add to new batch in same warehouse
        StockPoint.objects.create(warehouse=sp.warehouse, batch=new_batch, quantity=take)
        remaining -= take

    return Response({
        'message': f'Split {split_qty} units from {original.batch_number} into {new_batch_number}.',
        'original_batch': str(original.id),
        'new_batch': str(new_batch.id),
    }, status=status.HTTP_201_CREATED)


# ── Price Lists ────────────────────────────────────────────────

class PriceListListCreateView(generics.ListCreateAPIView):
    serializer_class = PriceListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PriceList.objects.filter(created_by=self.request.user).order_by('-created_at')


class PriceListDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PriceListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PriceList.objects.filter(created_by=self.request.user)


# ── Schemes ────────────────────────────────────────────────────

class SchemeListCreateView(generics.ListCreateAPIView):
    serializer_class = SchemeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Scheme.objects.filter(created_by=self.request.user).order_by('-start_date')


class SchemeDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SchemeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Scheme.objects.filter(created_by=self.request.user)

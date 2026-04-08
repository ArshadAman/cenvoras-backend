import csv
from io import TextIOWrapper

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from django.db.models import Sum, F
from .models import Product, Warehouse, StockPoint, StockTransfer, ProductBatch
from .models_pricing import PriceList, Scheme
from .serializers import (
    ProductSerializer, WarehouseSerializer, StockPointSerializer, 
    StockTransferSerializer, ProductBatchSerializer,
    PriceListSerializer, SchemeSerializer

)
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import filters


def _product_template_fields():
    fields = []
    for field in Product._meta.concrete_fields:
        if field.name in {'id', 'created_by'}:
            continue
        if not field.editable:
            continue
        # Expose user-facing pricing key while keeping DB column intact.
        fields.append('cost_price' if field.name == 'price' else field.name)
    return fields

class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'description', 'hsn_sac_code']

    def get_queryset(self):
        return Product.objects.filter(created_by=self.request.user.active_tenant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_tenant)

class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return Product.objects.none()
        return Product.objects.filter(created_by=self.request.user.active_tenant)

    def destroy(self, request, *args, **kwargs):
        from django.db.models.deletion import ProtectedError
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            return Response(
                {"error": "This product cannot be deleted because it is linked to existing invoices, purchases, or stock transfers."},
                status=status.HTTP_400_BAD_REQUEST
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        if not serializer.is_valid():
            print("==== PRODUCT UPDATE VALIDATION ERROR ====")
            print(serializer.errors)
            print("=========================================")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

class ProductBatchListView(generics.ListAPIView):
    serializer_class = ProductBatchSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Corrected `request.user` to `self.request.user` for class-based view
        # Removed `perform_create` as it's a ListAPIView
        return ProductBatch.objects.select_related('product').filter(product__created_by=self.request.user.active_tenant.active_tenant)

class WarehouseListCreateView(generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(created_by=self.request.user.active_tenant)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.active_tenant)


# Feature 2: Multi-Store / Godown — Detail/Update/Delete
class WarehouseDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return Warehouse.objects.none()
        return Warehouse.objects.filter(created_by=self.request.user.active_tenant)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def stock_point_list(request):
    """
    List stock points (inventory levels per batch per warehouse)
    Optional filters: ?product=UUID & ?warehouse=UUID
    """
    queryset = StockPoint.objects.all()
    
    # Filter by user's warehouses
    user_warehouses = Warehouse.objects.filter(created_by=request.user.active_tenant)
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
        user_warehouses = Warehouse.objects.filter(created_by=self.request.user.active_tenant)
        return StockPoint.objects.select_related('batch', 'warehouse', 'batch__product').filter(warehouse__in=user_warehouses)

class StockTransferListCreateView(generics.ListCreateAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockTransfer.objects.filter(created_by=self.request.user.active_tenant).order_by('-transfer_date')

class StockTransferDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return StockTransfer.objects.none()
        return StockTransfer.objects.filter(created_by=self.request.user.active_tenant)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def batch_list(request):
    """
    List product batches.
    Optional filters: ?product=UUID &search=name
    """
    queryset = ProductBatch.objects.filter(
        product__created_by=request.user.active_tenant.active_tenant
    ).select_related('product').order_by('-created_at')
    
    product_id = request.query_params.get('product')
    if product_id:
        queryset = queryset.filter(product__id=product_id)

    search = request.query_params.get('search', '')
    if search:
        queryset = queryset.filter(product__name__icontains=search)

    from cenvoras.pagination import StandardResultsSetPagination
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)
    if page is not None:
        serializer = ProductBatchSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
        
    serializer = ProductBatchSerializer(queryset, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def download_product_csv_template(request):
    """
    Download a CSV template for bulk product upload.
    The header is generated from Product model fields.
    """
    headers = _product_template_fields()
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="product_bulk_template.csv"'

    writer = csv.writer(response)
    writer.writerow(headers)
    # Add one sample row for quick guidance in spreadsheet tools.
    sample = ['' for _ in headers]
    if 'unit' in headers:
        sample[headers.index('unit')] = 'pcs'
    if 'conversion_factor' in headers:
        sample[headers.index('conversion_factor')] = '1'
    writer.writerow(sample)

    return response


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_upload_products(request):
    """
    Bulk create products from a CSV file.
    Expected file form key: file
    """
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return Response({'error': 'CSV file is required using form key "file".'}, status=status.HTTP_400_BAD_REQUEST)

    if not uploaded_file.name.lower().endswith('.csv'):
        return Response({'error': 'Only CSV files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

    def normalize_key(key):
        return (key or '').strip().lower().replace(' ', '_').replace('-', '_')

    header_aliases = {
        'cost_price': ['cost_price', 'price', 'purchase_price', 'cost'],
        'sale_price': ['sale_price', 'sales_price', 'selling_price', 'saleprice', 'salesprice'],
        'hsn_sac_code': ['hsn_sac_code', 'hsn_code', 'hsn'],
        'low_stock_alert': ['low_stock_alert', 'min_stock_level', 'reorder_level'],
        'stock': ['stock', 'opening_stock', 'current_stock'],
        'secondary_unit': ['secondary_unit', 'secondaryunit'],
        'conversion_factor': ['conversion_factor', 'conversionfactor'],
    }

    expected_fields = _product_template_fields()
    optional_nullable_fields = {'hsn_sac_code', 'description', 'secondary_unit', 'sale_price'}

    try:
        text_stream = TextIOWrapper(uploaded_file.file, encoding='utf-8-sig')
        reader = csv.DictReader(text_stream)
    except Exception:
        return Response({'error': 'Unable to parse CSV file.'}, status=status.HTTP_400_BAD_REQUEST)

    if not reader.fieldnames:
        return Response({'error': 'CSV must include a header row.'}, status=status.HTTP_400_BAD_REQUEST)

    created_count = 0
    errors = []

    processed_rows = 0

    with transaction.atomic():
        for index, row in enumerate(reader, start=2):
            normalized_row = {normalize_key(k): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            if not any(v not in (None, '') for v in normalized_row.values()):
                continue

            processed_rows += 1

            payload = {}
            for field in expected_fields:
                lookup_key = 'cost_price' if field == 'cost_price' else field
                value = normalized_row.get(lookup_key)
                if value in (None, ''):
                    for alias in header_aliases.get(lookup_key, []):
                        alias_value = normalized_row.get(alias)
                        if alias_value not in (None, ''):
                            value = alias_value
                            break

                if value in (None, ''):
                    if field in optional_nullable_fields:
                        payload[field] = None
                    continue

                if field == 'unit' and isinstance(value, str):
                    value = value.lower()

                payload[field] = value

            serializer = ProductSerializer(data=payload, context={'request': request})
            if serializer.is_valid():
                serializer.save(created_by=request.user.active_tenant)
                created_count += 1
            else:
                errors.append({'row': index, 'errors': serializer.errors})

    if errors:
        return Response(
            {
                'created_count': created_count,
                'failed_count': len(errors),
                'processed_rows': processed_rows,
                'errors': errors,
                'expected_columns': expected_fields,
            },
            status=status.HTTP_207_MULTI_STATUS,
        )

    if created_count == 0:
        return Response(
            {
                'error': 'No products were created from the CSV file.',
                'processed_rows': processed_rows,
                'expected_columns': expected_fields,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            'message': 'Bulk upload completed successfully.',
            'created_count': created_count,
        },
        status=status.HTTP_201_CREATED,
    )


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
        product__created_by=request.user.active_tenant.active_tenant,
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
        created_by=request.user.active_tenant,
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
    raw_split_qty = request.data.get('split_quantity', 0)
    try:
        split_qty = int(raw_split_qty)
    except (TypeError, ValueError):
        return Response({'error': 'split_quantity must be a valid integer.'}, status=status.HTTP_400_BAD_REQUEST)
    manufacturing_date = request.data.get('manufacturing_date')
    expiry_date = request.data.get('expiry_date')
    notes = request.data.get('notes')

    if not batch_id or not new_batch_number or split_qty <= 0:
        return Response({'error': 'batch_id, new_batch_number, and split_quantity (>0) are required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        original = ProductBatch.objects.get(id=batch_id, product__created_by=request.user.active_tenant)
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
        expiry_date=expiry_date if expiry_date else original.expiry_date,
        manufacturing_date=manufacturing_date if manufacturing_date else original.manufacturing_date,
        notes=notes if notes else original.notes,
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
        return PriceList.objects.filter(created_by=self.request.user.active_tenant).order_by('-created_at')


class PriceListDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PriceListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return PriceList.objects.none()
        return PriceList.objects.filter(created_by=self.request.user.active_tenant)


# ── Schemes ────────────────────────────────────────────────────

class SchemeListCreateView(generics.ListCreateAPIView):
    serializer_class = SchemeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Scheme.objects.filter(created_by=self.request.user.active_tenant).order_by('-start_date')


class SchemeDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SchemeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False): return Scheme.objects.none()
        return Scheme.objects.filter(created_by=self.request.user.active_tenant)


# =============================================================================
# Feature: Warranty Tracking Report
# =============================================================================
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def warranty_report(request):
    """
    Returns all sold products that have a warranty.
    Shows warranty start date (= invoice date), warranty end date,
    and a human-readable countdown like "2 years 10 months 5 days left".
    """
    from billing.models import SalesInvoice, SalesInvoiceItem
    from dateutil.relativedelta import relativedelta

    today = timezone.now().date()

    # Find all sales invoice items where the product has a warranty > 0
    items = SalesInvoiceItem.objects.filter(
        sales_invoice__created_by=request.user.active_tenant,
        product__warranty_months__gt=0,
    ).select_related('sales_invoice', 'product').order_by('-sales_invoice__invoice_date')

    results = []
    for item in items:
        warranty_months = item.product.warranty_months
        start_date = item.sales_invoice.invoice_date
        end_date = start_date + relativedelta(months=warranty_months)
        
        delta = relativedelta(end_date, today)
        
        if end_date < today:
            countdown = "Expired"
            status = "expired"
            days_left = (end_date - today).days
        else:
            days_left = (end_date - today).days
            parts = []
            if delta.years > 0:
                parts.append(f"{delta.years} year{'s' if delta.years > 1 else ''}")
            if delta.months > 0:
                parts.append(f"{delta.months} month{'s' if delta.months > 1 else ''}")
            if delta.days > 0:
                parts.append(f"{delta.days} day{'s' if delta.days > 1 else ''}")
            countdown = " ".join(parts) + " left" if parts else "Expiring today"
            
            if days_left <= 30:
                status = "critical"
            elif days_left <= 90:
                status = "warning"
            else:
                status = "active"

        results.append({
            'invoice_id': str(item.sales_invoice.id),
            'invoice_number': item.sales_invoice.invoice_number,
            'invoice_date': start_date,
            'customer_name': item.sales_invoice.customer_name or (item.sales_invoice.customer.name if item.sales_invoice.customer else 'N/A'),
            'product_id': str(item.product.id),
            'product_name': item.product.name,
            'quantity': item.quantity,
            'warranty_months': warranty_months,
            'warranty_start': start_date,
            'warranty_end': end_date,
            'days_left': days_left,
            'countdown': countdown,
            'status': status,
        })

    # Stats
    active_count = sum(1 for r in results if r['status'] == 'active')
    warning_count = sum(1 for r in results if r['status'] == 'warning')
    critical_count = sum(1 for r in results if r['status'] == 'critical')
    expired_count = sum(1 for r in results if r['status'] == 'expired')

    return Response({
        'count': len(results),
        'active_count': active_count,
        'warning_count': warning_count,
        'critical_count': critical_count,
        'expired_count': expired_count,
        'results': results,
    })


# =============================================================================
# Feature: Expiry Dashboard Summary (for dashboard card)
# =============================================================================
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def expiry_dashboard_summary(request):
    """
    Returns a summary for the dashboard card:
    - Count of batches expiring within N days (default 30, configurable via ?days=N)
    - Total value at risk
    Used by the "Expiring Soon" card on the Dashboard.
    """
    today = timezone.now().date()
    days = int(request.query_params.get('days', 30))
    cutoff = today + timedelta(days=days)

    batches = ProductBatch.objects.filter(
        product__created_by=request.user.active_tenant.active_tenant,
        expiry_date__isnull=False,
        expiry_date__lte=cutoff,
        is_active=True,
    ).select_related('product')

    count = 0
    total_value = 0
    items = []
    for batch in batches:
        total_qty = batch.stock_points.aggregate(total=Sum('quantity'))['total'] or 0
        if total_qty <= 0:
            continue
        days_left = (batch.expiry_date - today).days
        value = float(batch.mrp * total_qty)
        count += 1
        total_value += value
        items.append({
            'batch_id': str(batch.id),
            'product_name': batch.product.name,
            'batch_number': batch.batch_number,
            'expiry_date': batch.expiry_date,
            'days_left': days_left,
            'status': 'expired' if days_left < 0 else ('critical' if days_left <= 7 else 'warning'),
            'quantity': total_qty,
            'value': value,
        })

    # Sort by days_left ascending (most urgent first)
    items.sort(key=lambda x: x['days_left'])

    return Response({
        'count': count,
        'total_value': round(total_value, 2),
        'items': items,
    })


from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductBatch, StockPoint, Warehouse
from .models_sidecar import StockJournal, StockJournalItem
from .serializers_sidecar import StockJournalSerializer


def _get_tenant(request):
    """Return the active tenant for multi-tenancy support."""
    return getattr(request.user, 'active_tenant', None) or request.user


class StockAdjustmentView(APIView):
    """
    API endpoint for manual stock adjustments and adjustment history.
    POST: Atomically adjusts stock with row-level locks, updates StockPoint,
          synchronizes Product.stock, and writes an audit StockJournal entry.
    GET:  Returns adjustment journals optimized with select_related and prefetch_related (no N+1 queries).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        tenant = _get_tenant(request)
        journals = (
            StockJournal.objects.filter(created_by=tenant)
            .select_related('warehouse')
            .prefetch_related('items__product', 'items__batch')
            .order_by('-date', '-created_at')
        )

        product_id = request.query_params.get('product_id')
        if product_id:
            journals = journals.filter(items__product_id=product_id)

        warehouse_id = request.query_params.get('warehouse_id')
        if warehouse_id:
            journals = journals.filter(warehouse_id=warehouse_id)

        search = request.query_params.get('search', '').strip()
        if search:
            journals = journals.filter(
                Q(voucher_no__icontains=search)
                | Q(notes__icontains=search)
                | Q(items__product__name__icontains=search)
            ).distinct()

        serializer = StockJournalSerializer(journals, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        tenant = _get_tenant(request)
        data = request.data

        product_id = data.get('product_id')
        if not product_id:
            return Response(
                {"message": "product_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        adjustment_type = data.get('adjustment_type', '').lower()
        if adjustment_type not in ('add', 'remove', 'set'):
            return Response(
                {"message": "adjustment_type must be 'add', 'remove', or 'set'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            quantity = float(data.get('quantity', 0))
            if quantity < 0:
                raise ValueError()
        except (TypeError, ValueError):
            return Response(
                {"message": "quantity must be a non-negative number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qty_int = int(round(quantity))
        reason = data.get('reason', 'manual_adjustment')
        notes = data.get('notes', '')
        warehouse_id = data.get('warehouse_id')
        batch_id = data.get('batch_id')

        with transaction.atomic():
            # Concurrency protection: Lock the product row
            try:
                product = Product.objects.select_for_update().get(
                    id=product_id, created_by=tenant
                )
            except Product.DoesNotExist:
                return Response(
                    {"message": "Product not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Resolve warehouse
            if warehouse_id:
                try:
                    warehouse = Warehouse.objects.get(
                        id=warehouse_id, created_by=tenant
                    )
                except Warehouse.DoesNotExist:
                    return Response(
                        {"message": "Specified warehouse not found."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                warehouse = (
                    Warehouse.objects.filter(created_by=tenant, is_active=True).first()
                )
                if not warehouse:
                    warehouse = Warehouse.objects.create(
                        name="Main Warehouse",
                        created_by=tenant,
                        is_active=True,
                    )

            # Resolve batch
            had_prior_batches = ProductBatch.objects.filter(product=product).exists()
            if batch_id:
                try:
                    batch = ProductBatch.objects.get(id=batch_id, product=product)
                except ProductBatch.DoesNotExist:
                    return Response(
                        {"message": "Specified batch not found."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                batch = ProductBatch.objects.filter(product=product).first()
                if not batch:
                    batch = ProductBatch.objects.create(
                        product=product,
                        batch_number="DEFAULT",
                        sale_price=product.sale_price or 0,
                        cost_price=product.price or 0,
                    )

            # Resolve StockPoint with row-level lock
            # If product previously had unbatched stock and this is its first batch/stockpoint,
            # seed the stock point with existing product.stock so unbatched stock is preserved.
            initial_stock_seed = product.stock if not had_prior_batches else 0
            stock_point, _ = StockPoint.objects.select_for_update().get_or_create(
                warehouse=warehouse,
                batch=batch,
                defaults={'quantity': initial_stock_seed},
            )

            current_sp_qty = stock_point.quantity
            if adjustment_type == 'add':
                new_sp_qty = current_sp_qty + qty_int
                qty_delta = qty_int
            elif adjustment_type == 'remove':
                new_sp_qty = max(0, current_sp_qty - qty_int)
                qty_delta = new_sp_qty - current_sp_qty
            elif adjustment_type == 'set':
                new_sp_qty = max(0, qty_int)
                qty_delta = new_sp_qty - current_sp_qty

            stock_point.quantity = new_sp_qty
            stock_point.save()

            # Synchronize product cached stock
            product.recalculate_stock(save=True)

            # Record audit StockJournal and StockJournalItem
            now = timezone.now()
            voucher_no = f"ADJ-{now.strftime('%Y%m%d%H%M%S')}-{product.id.hex[:4].upper()}"
            journal = StockJournal.objects.create(
                date=now.date(),
                voucher_no=voucher_no,
                warehouse=warehouse,
                adjustment_type=reason,
                notes=notes or f"Manual adjustment ({adjustment_type}): {quantity}",
                created_by=tenant,
            )
            StockJournalItem.objects.create(
                journal=journal,
                product=product,
                batch=batch,
                quantity=qty_delta,
            )

        return Response(
            {
                "success": True,
                "message": "Stock adjustment recorded successfully!",
                "product_id": str(product.id),
                "product_name": product.name,
                "current_stock": product.stock,
                "voucher_no": journal.voucher_no,
                "delta": qty_delta,
            },
            status=status.HTTP_200_OK,
        )

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q, F
from .models_sidecar import SalesOrder, SalesOrderItem, DeliveryChallan, DeliveryChallanItem, InvoiceSettings, Quotation, QuotationItem, TransactionMeta
from .serializers_sidecar import SalesOrderSerializer, DeliveryChallanSerializer, InvoiceSettingsSerializer, QuotationSerializer
from .models import SalesInvoice, SalesInvoiceItem, Customer
from inventory.models import Product
from cenvoras.pagination import StandardResultsSetPagination
from datetime import date
from decimal import Decimal

# =============================================================================
# SALES ORDER VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sales_order_list_create(request):
    tenant = request.user.active_tenant
    if request.method == 'GET':
        search = request.GET.get('search', '')
        orders = SalesOrder.objects.filter(created_by=tenant).select_related('customer').prefetch_related('items__product')
        
        if search:
            orders = orders.filter(
                Q(order_number__icontains=search) | Q(customer__name__icontains=search)
            )
            
        orders = orders.order_by('-date')
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(orders, request)
        if page is not None:
            serializer = SalesOrderSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = SalesOrderSerializer(orders, many=True)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = SalesOrderSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_order_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesOrder.DoesNotExist:
        return Response({"success": False, "message": "Order not found"}, status=404)
        
    if request.method == 'GET':
        from billing.sync_service import DocumentSyncService
        DocumentSyncService.reconcile_sales_order_dispatched_state(order, tenant)
        if hasattr(order, '_prefetched_objects_cache'):
            order._prefetched_objects_cache.clear()
        order.refresh_from_db()
        serializer = SalesOrderSerializer(order)
        return Response(serializer.data)
        
    elif request.method in ['PUT', 'PATCH']:
        serializer = SalesOrderSerializer(order, data=request.data, partial=(request.method == 'PATCH'), context={'request': request})
        if serializer.is_valid():
            serializer.save()
            if hasattr(order, '_prefetched_objects_cache'):
                order._prefetched_objects_cache.clear()
            order.refresh_from_db()
            return Response(SalesOrderSerializer(order).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
    elif request.method == 'DELETE':
        source_quotation = getattr(order, 'source_quotation', None)
        if source_quotation:
            source_quotation.status = 'pending'
            source_quotation.save(update_fields=['status'])
            source_quotation.items.update(converted_to_order=False)
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_order_to_invoice(request, pk):
    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesOrder.DoesNotExist:
        return Response({"message": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

    # Check if an invoice was already generated for this sales order
    existing_invoice = SalesInvoice.objects.filter(created_by=tenant, po_number=order.order_number).first()
    if existing_invoice:
        return Response({"message": f"Order has already been converted to an invoice (#{existing_invoice.invoice_number})."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # Lock order row for update
        order = SalesOrder.objects.select_for_update().select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
        existing_invoice = SalesInvoice.objects.filter(created_by=tenant, po_number=order.order_number).first()
        if existing_invoice:
            return Response({"message": f"Order has already been converted to an invoice (#{existing_invoice.invoice_number})."}, status=status.HTTP_400_BAD_REQUEST)

        # Reconcile any drift in dispatched_quantity prior to conversion
        from billing.sync_service import DocumentSyncService
        DocumentSyncService.reconcile_sales_order_dispatched_state(order, tenant)
        if hasattr(order, '_prefetched_objects_cache'):
            order._prefetched_objects_cache.clear()
        order.refresh_from_db()

        # Parse requested conversion items & quantities
        items_payload = request.data.get('items')
        order_items_map = {item.id: item for item in order.items.all()}
        
        items_to_invoice = []
        is_post_delivery_invoicing = False
        all_dispatched = all((item.dispatched_quantity or 0) >= item.quantity for item in order.items.all())

        if items_payload and isinstance(items_payload, list) and len(items_payload) > 0:
            for entry in items_payload:
                raw_id = entry.get('id') or entry.get('item_id')
                if not raw_id:
                    continue
                try:
                    item_id = int(raw_id)
                except (ValueError, TypeError):
                    continue
                
                order_item = order_items_map.get(item_id)
                if not order_item:
                    continue
                
                try:
                    qty = int(entry.get('quantity', 0))
                except (ValueError, TypeError):
                    qty = 0
                
                if qty <= 0:
                    continue
                
                pending_qty = order_item.pending_quantity
                if pending_qty <= 0 and all_dispatched:
                    # Items were already dispatched via Delivery Challan; invoicing delivered quantity
                    is_post_delivery_invoicing = True
                    items_to_invoice.append((order_item, min(qty, order_item.quantity)))
                else:
                    if qty > pending_qty:
                        return Response(
                            {"message": f"Cannot invoice {qty} for {order_item.product.name}. Only {pending_qty} pending in order."},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    items_to_invoice.append((order_item, qty))
        else:
            # Fallback: invoice all items
            for item in order.items.all():
                if item.pending_quantity > 0:
                    items_to_invoice.append((item, item.pending_quantity))
                elif all_dispatched:
                    # All items were delivered via Challan; invoice full order
                    is_post_delivery_invoicing = True
                    items_to_invoice.append((item, item.quantity))

        if not items_to_invoice:
            return Response({"message": "No valid items selected for invoice."}, status=status.HTTP_400_BAD_REQUEST)

        # Calculate estimated total for credit check
        est_total = Decimal('0.00')
        for order_item, inv_qty in items_to_invoice:
            b_amt = Decimal(str(inv_qty)) * Decimal(str(order_item.price))
            d_amt = (b_amt * Decimal(str(order_item.discount or 0))) / Decimal('100')
            t_base = b_amt - d_amt
            t_amt = (t_base * Decimal(str(order_item.tax or 0))) / Decimal('100')
            est_total += (t_base + t_amt).quantize(Decimal('0.01'))

        # Check credit limit if allow_credit is disabled
        if order.customer and not order.customer.allow_credit:
            new_balance = order.customer.current_balance + est_total
            if new_balance > order.customer.credit_limit:
                return Response(
                    {"message": f"Credit limit exceeded. Current: {order.customer.current_balance}, Limit: {order.customer.credit_limit}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        from billing.sequence_service import allocate_next_number
        next_invoice_number = allocate_next_number(tenant, document_type='sales_invoice')

        invoice = SalesInvoice(
            customer=order.customer,
            customer_name=order.customer.name if order.customer else '',
            customer_address=order.customer.address if order.customer else '',
            place_of_supply=order.customer.state if order.customer and order.customer.state else None,
            invoice_number=next_invoice_number,
            invoice_date=date.today(),
            po_number=order.po_number or order.order_number,
            po_date=order.po_date,
            source_sales_order=order,
            created_by=tenant,
            total_amount=Decimal('0.00'),
            status='final',
        )
        if is_post_delivery_invoicing:
            invoice._skip_stock_deduction = True

        linked_challans = list(DeliveryChallan.objects.filter(sales_order=order, created_by=tenant))
        if linked_challans:
            invoice.challan_number = ", ".join(c.challan_number for c in linked_challans)
            invoice.challan_date = max(c.date for c in linked_challans)

        invoice.save()

        if linked_challans:
            for c in linked_challans:
                c.is_billed = True
                c.status = 'billed'
                c.converted_invoice = invoice
                c.save(update_fields=['is_billed', 'status', 'converted_invoice'])

        raw_items = []
        for order_item, inv_qty in items_to_invoice:
            item_unit = getattr(order_item, 'unit', None) or (order_item.product.unit if order_item.product else 'pcs') or 'pcs'
            item_tax = getattr(order_item, 'tax', None) if getattr(order_item, 'tax', None) is not None else (order_item.product.tax if order_item.product else Decimal('0.00'))
            item_discount = getattr(order_item, 'discount', Decimal('0.00')) or Decimal('0.00')
            
            b_amt = Decimal(str(inv_qty)) * Decimal(str(order_item.price))
            d_amt = (b_amt * Decimal(str(item_discount))) / Decimal('100')
            taxable = b_amt - d_amt
            tax_amt = (taxable * Decimal(str(item_tax))) / Decimal('100')
            line_amount = (taxable + tax_amt).quantize(Decimal('0.01'))

            raw_items.append({
                'product': order_item.product,
                'quantity': inv_qty,
                'price': order_item.price,
                'amount': line_amount,
                'unit': item_unit,
                'tax': item_tax,
                'discount': item_discount,
                'free_quantity': 0,
                'order_item': order_item,
            })

        # Evaluate active schemes if any items have free_quantity == 0
        bonus_items = []
        try:
            from billing.scheme_service import evaluate_schemes_for_items
            scheme_res = evaluate_schemes_for_items(tenant=tenant, items=raw_items, evaluation_date=invoice.invoice_date)
            raw_items = scheme_res.get('items', raw_items)
            bonus_items = scheme_res.get('additional_free_items', [])
        except Exception:
            pass

        for r_item in raw_items:
            inv_item = SalesInvoiceItem(
                sales_invoice=invoice,
                product=r_item['product'],
                quantity=r_item['quantity'],
                free_quantity=r_item.get('free_quantity', 0),
                price=r_item['price'],
                amount=r_item['amount'],
                unit=r_item['unit'],
                tax=r_item['tax'],
                discount=r_item['discount'],
                source_item_id=str(r_item['order_item'].id) if r_item.get('order_item') else None,
            )
            if is_post_delivery_invoicing:
                inv_item._skip_stock_deduction = True
            inv_item.save()

            # Update order_item dispatched_quantity if not already dispatched
            o_item = r_item.get('order_item')
            if o_item and not is_post_delivery_invoicing:
                o_item.dispatched_quantity = (o_item.dispatched_quantity or 0) + r_item['quantity']
                o_item.save(update_fields=['dispatched_quantity'])

        for b_item in bonus_items:
            b_prod = b_item.get('product')
            if b_prod:
                SalesInvoiceItem.objects.create(
                    sales_invoice=invoice,
                    product=b_prod,
                    quantity=b_item.get('quantity', 0),
                    free_quantity=b_item.get('free_quantity', 0),
                    price=Decimal('0.00'),
                    amount=Decimal('0.00'),
                    unit=b_item.get('unit') or 'pcs',
                    tax=Decimal('0.00'),
                    discount=Decimal('0.00'),
                )

        invoice.refresh_from_db()

        # Ensure TransactionMeta exists
        TransactionMeta.objects.get_or_create(invoice=invoice)

        # Accrue loyalty points (1 point per ₹100)
        if order.customer and hasattr(order.customer, 'meta'):
            try:
                points_earned = int(invoice.total_amount / 100)
                if points_earned > 0:
                    order.customer.meta.loyalty_points += points_earned
                    order.customer.meta.save(update_fields=['loyalty_points'])
            except Exception:
                pass

        # Update customer balance
        if invoice.status == 'final' and invoice.customer_id:
            Customer.objects.filter(pk=invoice.customer_id).update(
                current_balance=F('current_balance') + invoice.total_amount
            )

        # Rebuild general ledger entries
        from .serializers import _rebuild_sales_invoice_ledger
        _rebuild_sales_invoice_ledger(invoice.id)


        from billing.sequence_service import sync_sequence_after_creation, get_tenant_full_prefix
        full_prefix = get_tenant_full_prefix(tenant, document_type='sales_invoice')
        sync_sequence_after_creation(tenant, 'sales_invoice', full_prefix, invoice.invoice_number)


        # Update Order Stage based on fulfillment of all items
        all_order_items = list(SalesOrderItem.objects.filter(order=order))
        all_fulfilled = all(item.is_fulfilled for item in all_order_items)
        any_dispatched = any((item.dispatched_quantity or 0) > 0 for item in all_order_items)

        if all_fulfilled:
            order.stage = 'completed'
        elif any_dispatched:
            order.stage = 'shipped'
        order.save(update_fields=['stage'])

    return Response({"message": "Converted successfully", "invoice_id": invoice.id, "invoice_number": invoice.invoice_number})

# =============================================================================
# DELIVERY CHALLAN VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def delivery_challan_list_create(request):
    tenant = request.user.active_tenant
    if request.method == 'GET':
        search = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '').strip()
        ordering = request.GET.get('ordering', '-date').strip() or '-date'

        challans = DeliveryChallan.objects.filter(created_by=tenant).select_related('customer', 'warehouse', 'sales_order').prefetch_related('items__product', 'items__batch')
        
        if search:
            challans = challans.filter(
                Q(challan_number__icontains=search) | 
                Q(customer__name__icontains=search) |
                Q(customer_name__icontains=search) |
                Q(sales_order__order_number__icontains=search) |
                Q(vehicle_number__icontains=search)
            )

        if status_filter and status_filter != 'all':
            if status_filter == 'open':
                challans = challans.filter(is_billed=False, status='open')
            elif status_filter in ['billed', 'invoiced']:
                challans = challans.filter(Q(is_billed=True) | Q(status='billed'))
            else:
                challans = challans.filter(status=status_filter)
            
        challans = challans.order_by(ordering)
        
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(challans, request)
        if page is not None:
            serializer = DeliveryChallanSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = DeliveryChallanSerializer(challans, many=True)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = DeliveryChallanSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            challan = serializer.save()
            return Response(DeliveryChallanSerializer(challan).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

from collections import defaultdict
from django.db.models import Case, When, Value, F, Q, Count, DecimalField
from django.db.models.functions import Greatest
from inventory.models import StockPoint

def batch_revert_challan_dispatches_and_stocks(challans, tenant):
    """
    Batch reverts inventory stock and linked SalesOrder dispatched quantities.
    STRICTLY ZERO N+1 QUERIES: Uses in-memory aggregations and single-pass batch SQL.
    """
    challan_ids = [c.id for c in challans]
    if not challan_ids:
        return

    items = list(DeliveryChallanItem.objects.filter(
        challan_id__in=challan_ids
    ).values(
        'product_id',
        'batch_id',
        'quantity',
        'free_quantity',
        'source_item_id',
        'challan__warehouse_id',
        'challan__sales_order_id',
        'row_type'
    ))

    if not items:
        return

    # In-memory accumulators
    product_deltas = defaultdict(lambda: Decimal('0.00'))
    stock_point_deltas = defaultdict(lambda: Decimal('0.00'))  # (batch_id, warehouse_id) -> delta
    so_item_deltas = defaultdict(lambda: Decimal('0.00'))      # source_item_id -> delta
    affected_so_ids = set()

    for item in items:
        if item.get('row_type') == 'note':
            continue

        qty = Decimal(str(item.get('quantity') or 0)) + Decimal(str(item.get('free_quantity') or 0))
        prod_id = item.get('product_id')
        batch_id = item.get('batch_id')
        wh_id = item.get('challan__warehouse_id')
        src_item_id = item.get('source_item_id')
        so_id = item.get('challan__sales_order_id')

        if prod_id and qty > 0:
            product_deltas[prod_id] += qty
            if batch_id and wh_id:
                stock_point_deltas[(batch_id, wh_id)] += qty

        if src_item_id and qty > 0:
            so_item_deltas[src_item_id] += qty
            if so_id:
                affected_so_ids.add(so_id)

    # 1. Batch Update Product stock (Single query using CASE/WHEN)
    if product_deltas:
        cases = [
            When(id=pid, then=F('stock') + delta)
            for pid, delta in product_deltas.items()
        ]
        Product.objects.filter(id__in=product_deltas.keys(), created_by=tenant).update(
            stock=Case(*cases, default=F('stock'), output_field=DecimalField())
        )

    # 2. Batch Update StockPoints (Single query using CASE/WHEN)
    if stock_point_deltas:
        sp_filter = Q()
        cases = []
        for (bid, wid), delta in stock_point_deltas.items():
            sp_filter |= Q(batch_id=bid, warehouse_id=wid)
            cases.append(When(batch_id=bid, warehouse_id=wid, then=F('quantity') + delta))

        StockPoint.objects.filter(sp_filter).update(
            quantity=Case(*cases, default=F('quantity'), output_field=DecimalField())
        )

    # 3. Batch Revert SalesOrderItem dispatched_quantity (Single query using CASE/WHEN with Greatest())
    if so_item_deltas:
        from django.db.models import PositiveIntegerField
        cases = [
            When(id=item_id, then=Greatest(F('dispatched_quantity') - int(delta), 0))
            for item_id, delta in so_item_deltas.items()
        ]
        SalesOrderItem.objects.filter(id__in=so_item_deltas.keys()).update(
            dispatched_quantity=Case(*cases, default=F('dispatched_quantity'), output_field=PositiveIntegerField())
        )

    # 4. Batch Recalculate SalesOrder stages for all affected Sales Orders (Single aggregated query)
    if affected_so_ids:
        order_item_stats = SalesOrderItem.objects.filter(
            order_id__in=affected_so_ids
        ).values('order_id').annotate(
            total_items=Count('id'),
            fulfilled_items=Count('id', filter=Q(dispatched_quantity__gte=F('quantity'), quantity__gt=0)),
            partially_dispatched_items=Count('id', filter=Q(dispatched_quantity__gt=0))
        )

        stage_cases = []
        for stat in order_item_stats:
            so_id = stat['order_id']
            tot = stat['total_items']
            ful = stat['fulfilled_items']
            part = stat['partially_dispatched_items']

            if tot > 0 and ful == tot:
                new_stage = 'completed'
            elif part > 0:
                new_stage = 'shipped'
            else:
                new_stage = 'new'
            stage_cases.append(When(id=so_id, then=Value(new_stage)))

        if stage_cases:
            SalesOrder.objects.filter(id__in=affected_so_ids, created_by=tenant).update(
                stage=Case(*stage_cases, default=F('stage'))
            )


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def delivery_challan_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        challan = DeliveryChallan.objects.select_related('customer', 'warehouse', 'converted_invoice', 'sales_order').prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
    except DeliveryChallan.DoesNotExist:
        return Response({"message": "Delivery Challan not found"}, status=status.HTTP_404_NOT_FOUND)
        
    if request.method == 'GET':
        serializer = DeliveryChallanSerializer(challan)
        return Response(serializer.data)
    elif request.method in ['PUT', 'PATCH']:
        serializer = DeliveryChallanSerializer(challan, data=request.data, partial=(request.method == 'PATCH'), context={'request': request})
        if serializer.is_valid():
            serializer.save()
            if hasattr(challan, '_prefetched_objects_cache'):
                challan._prefetched_objects_cache.clear()
            challan.refresh_from_db()
            return Response(DeliveryChallanSerializer(challan).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if challan.is_billed:
            return Response({"message": "Cannot delete an invoiced delivery challan."}, status=status.HTTP_400_BAD_REQUEST)
        
        with transaction.atomic():
            # Revert stock and sales order dispatched status (Zero N+1)
            batch_revert_challan_dispatches_and_stocks([challan], tenant)
            challan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_delete_delivery_challans(request):
    """
    Bulk delete delivery challans with zero N+1 queries.
    Reverts stock and sales order dispatched status atomically.
    """
    tenant = request.user.active_tenant
    challan_ids = request.data.get('ids', [])
    
    if not challan_ids or not isinstance(challan_ids, list):
        return Response({'message': 'A list of challan IDs is required.'}, status=status.HTTP_400_BAD_REQUEST)
    
    with transaction.atomic():
        challans = list(DeliveryChallan.objects.filter(
            pk__in=challan_ids,
            created_by=tenant
        ).select_for_update(of=('self',)))
        
        if not challans:
            return Response({'message': 'No matching delivery challans found.'}, status=status.HTTP_404_NOT_FOUND)
        
        billed = [c.challan_number for c in challans if c.is_billed]
        if billed:
            return Response({
                'message': f"Cannot delete challan(s) {', '.join(billed)} because they are already converted to sales invoices."
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # 1. Batch revert inventory and SO items (constant queries)
        batch_revert_challan_dispatches_and_stocks(challans, tenant)
        
        # 2. Bulk delete in single SQL query
        count = len(challans)
        DeliveryChallan.objects.filter(pk__in=challan_ids, created_by=tenant).delete()
            
        return Response({'message': f'Successfully deleted {count} delivery challan(s).', 'count': count})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def delivery_challan_next_number(request):
    from billing.sequence_service import preview_next_number

    tenant = request.user.active_tenant
    prefix = request.GET.get('prefix', 'DC-')
    is_draft = request.GET.get('is_draft', 'false').lower() in ('true', '1')

    next_number, suffix = preview_next_number(
        tenant=tenant,
        document_type='delivery_challan',
        prefix=prefix,
        is_draft=is_draft,
    )

    return Response({
        'success': True,
        'next_number': next_number,
        'suffix': suffix,
    })



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_order_to_challan(request, pk):
    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesOrder.DoesNotExist:
        return Response({"message": "Order not found"}, status=status.HTTP_404_NOT_FOUND)

    if order.stage in ['completed', 'cancelled']:
        return Response({"message": f"Order has stage '{order.stage}' and cannot be converted."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        order = SalesOrder.objects.select_for_update().select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
        if order.stage in ['completed', 'cancelled']:
            return Response({"message": f"Order has stage '{order.stage}' and cannot be converted."}, status=status.HTTP_400_BAD_REQUEST)

        # Reconcile any drift in dispatched_quantity prior to conversion
        from billing.sync_service import DocumentSyncService
        DocumentSyncService.reconcile_sales_order_dispatched_state(order, tenant)
        if hasattr(order, '_prefetched_objects_cache'):
            order._prefetched_objects_cache.clear()
        order.refresh_from_db()

        from billing.sequence_service import allocate_next_number
        next_challan_number = allocate_next_number(tenant, document_type='delivery_challan', prefix='DC-')

        vehicle_number = (request.data.get('vehicle_number') or '').strip()
        transport_mode = (request.data.get('transport_mode') or '').strip()
        eway_bill_number = (request.data.get('eway_bill_number') or '').strip()
        custom_notes = (request.data.get('notes') or '').strip()

        challan = DeliveryChallan.objects.create(
            challan_number=next_challan_number,
            date=date.today(),
            customer=order.customer,
            customer_name=order.customer.name if order.customer else '',
            customer_address=order.customer.address if order.customer else '',
            delivery_address=order.customer.address if order.customer else '',
            vehicle_number=vehicle_number,
            transport_mode=transport_mode,
            eway_bill_number=eway_bill_number,
            sales_order=order,
            po_number=order.po_number or order.order_number,
            po_date=order.po_date,
            total_amount=Decimal('0.00'),
            status='open',
            notes=custom_notes or f"Converted from Sales Order {order.order_number}",
            created_by=tenant,
        )

        # Parse requested conversion items & quantities
        items_payload = request.data.get('items')
        order_items_map = {item.id: item for item in order.items.all()}
        
        items_to_dispatch = []
        if items_payload and isinstance(items_payload, list) and len(items_payload) > 0:
            for entry in items_payload:
                raw_id = entry.get('id') or entry.get('item_id')
                if not raw_id:
                    continue
                try:
                    item_id = int(raw_id)
                except (ValueError, TypeError):
                    continue
                
                order_item = order_items_map.get(item_id)
                if not order_item:
                    continue
                
                try:
                    qty = int(entry.get('quantity', 0))
                except (ValueError, TypeError):
                    qty = 0
                
                if qty <= 0:
                    continue
                
                pending_qty = order_item.pending_quantity
                if qty > pending_qty:
                    return Response(
                        {"message": f"Cannot dispatch {qty} for {order_item.product.name}. Only {pending_qty} pending in order."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                items_to_dispatch.append((order_item, qty))
        else:
            # Fallback: dispatch all remaining items with their pending quantities
            for item in order.items.all():
                if item.pending_quantity > 0:
                    items_to_dispatch.append((item, item.pending_quantity))

        if not items_to_dispatch:
            return Response({"message": "No valid pending items selected for dispatch."}, status=status.HTTP_400_BAD_REQUEST)

        total_amount = Decimal('0.00')
        from django.db.models import F

        for order_item, dispatch_qty in items_to_dispatch:
            item_unit = getattr(order_item, 'unit', None) or (order_item.product.unit if order_item.product else 'pcs') or 'pcs'
            item_tax = getattr(order_item, 'tax', None) if getattr(order_item, 'tax', None) is not None else (order_item.product.tax if order_item.product else Decimal('0.00'))
            item_discount = getattr(order_item, 'discount', Decimal('0.00')) or Decimal('0.00')
            
            base_amt = Decimal(str(dispatch_qty)) * Decimal(str(order_item.price))
            disc_amt = (base_amt * Decimal(str(item_discount))) / Decimal('100')
            taxable = base_amt - disc_amt
            tax_amt = (taxable * Decimal(str(item_tax))) / Decimal('100')
            line_amount = (taxable + tax_amt).quantize(Decimal('0.01'))
            total_amount += line_amount

            DeliveryChallanItem.objects.create(
                challan=challan,
                product=order_item.product,
                quantity=dispatch_qty,
                free_quantity=0,
                price=order_item.price,
                amount=line_amount,
                unit=item_unit,
                tax=item_tax,
                discount=item_discount,
                source_item_id=str(order_item.id),
            )

            # Deduct stock for dispatched item
            if dispatch_qty > 0:
                Product.objects.filter(pk=order_item.product_id).update(stock=F('stock') - dispatch_qty)

            # Update Sales Order item dispatched_quantity (NEVER delete the item!)
            order_item.dispatched_quantity = (order_item.dispatched_quantity or 0) + dispatch_qty
            order_item.save(update_fields=['dispatched_quantity'])

        challan.total_amount = total_amount
        challan.save(update_fields=['total_amount'])

        # Update order stage based on item fulfillment
        all_order_items = list(SalesOrderItem.objects.filter(order=order))
        all_fulfilled = all(item.is_fulfilled for item in all_order_items)
        any_dispatched = any((item.dispatched_quantity or 0) > 0 for item in all_order_items)

        if all_fulfilled:
            order.stage = 'completed'
        elif any_dispatched:
            order.stage = 'shipped'
        order.save(update_fields=['stage'])

    return Response({
        "message": "Converted to Delivery Challan successfully",
        "challan_id": str(challan.id),
        "challan_number": challan.challan_number,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_challan_to_invoice(request, pk):
    tenant = request.user.active_tenant
    try:
        challan = DeliveryChallan.objects.select_related('customer', 'warehouse', 'sales_order').prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
    except DeliveryChallan.DoesNotExist:
        return Response({"message": "Delivery Challan not found"}, status=status.HTTP_404_NOT_FOUND)

    if challan.is_billed:
        return Response({"message": "Delivery Challan has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            challan = DeliveryChallan.objects.select_for_update(of=('self',)).select_related(
                'customer', 'warehouse', 'sales_order'
            ).prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
            if challan.is_billed:
                return Response({"message": "Delivery Challan has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

            # Check credit limit
            if challan.customer and not challan.customer.allow_credit:
                curr_balance = challan.customer.current_balance or Decimal('0.00')
                cred_limit = challan.customer.credit_limit or Decimal('0.00')
                challan_tot = challan.total_amount or Decimal('0.00')
                new_balance = curr_balance + challan_tot
                if new_balance > cred_limit:
                    return Response(
                        {"message": f"Credit limit exceeded. Current: {curr_balance}, Limit: {cred_limit}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            from billing.sequence_service import allocate_next_number
            next_invoice_number = allocate_next_number(tenant, document_type='sales_invoice')

            invoice = SalesInvoice(
                customer=challan.customer,
                customer_name=challan.customer.name if challan.customer else (challan.customer_name or ''),
                customer_address=challan.customer_address or (challan.customer.address if challan.customer else ''),
                place_of_supply=challan.customer.state if challan.customer and challan.customer.state else None,
                invoice_number=next_invoice_number,
                invoice_date=date.today(),
                challan_number=challan.challan_number,
                challan_date=challan.date,
                delivery_address=challan.delivery_address,
                po_number=challan.po_number or (challan.sales_order.order_number if challan.sales_order else ''),
                po_date=challan.po_date,
                warehouse=challan.warehouse,
                source_sales_order=challan.sales_order,
                round_off=challan.round_off or Decimal('0.00'),
                total_amount=Decimal('0.00'),  # Set to 0.00 initially so item signals add up cleanly
                status='final',
                created_by=tenant,
            )
            # CRUCIAL: Set flag so signal does NOT deduct stock a second time!
            invoice._skip_stock_deduction = True
            invoice.save()

            total_items_amount = Decimal('0.00')
            for c_item in challan.items.all():
                is_note = getattr(c_item, 'row_type', 'item') == 'note' or not c_item.product_id
                prod = c_item.product
                if is_note:
                    price = Decimal('0.00')
                    qty = 0
                    discount = Decimal('0.00')
                    tax = Decimal('0.00')
                    line_amount = Decimal('0.00')
                    unit = ''
                    hsn_sac_code = ''
                else:
                    price = Decimal(str(
                        c_item.price if c_item.price is not None
                        else (getattr(prod, 'sale_price', None) or getattr(prod, 'price', None) or Decimal('0.00'))
                    ))
                    qty = int(c_item.quantity or 1)
                    discount = Decimal(str(c_item.discount or 0))
                    tax = Decimal(str(c_item.tax or 0))
                    base_amount = qty * price
                    discount_amount = (base_amount * discount) / Decimal('100')
                    taxable_amount = base_amount - discount_amount
                    tax_amount = (taxable_amount * tax) / Decimal('100')
                    line_amount = (taxable_amount + tax_amount).quantize(Decimal('0.01'))
                    total_items_amount += line_amount
                    unit = c_item.unit or getattr(prod, 'unit', 'pcs') or 'pcs'
                    hsn_sac_code = c_item.hsn_sac_code or getattr(prod, 'hsn_sac_code', '') or ''

                inv_item = SalesInvoiceItem(
                    sales_invoice=invoice,
                    product=prod,
                    row_type=getattr(c_item, 'row_type', 'item') or 'item',
                    batch=c_item.batch,
                    hsn_sac_code=hsn_sac_code,
                    quantity=qty,
                    free_quantity=c_item.free_quantity or 0,
                    price=price,
                    amount=line_amount,
                    unit=unit,
                    discount=discount,
                    tax=tax,
                    description=getattr(c_item, 'description', '') or '',
                    source_item_id=str(c_item.id),
                )
                inv_item._skip_stock_deduction = True
                inv_item.save()

            final_total = (total_items_amount + (challan.round_off or Decimal('0.00'))).quantize(Decimal('0.01'))
            invoice.total_amount = final_total
            invoice.save(update_fields=['total_amount'])

            # Ensure TransactionMeta exists
            TransactionMeta.objects.get_or_create(invoice=invoice)

            # Accrue loyalty points (1 point per ₹100)
            if challan.customer and hasattr(challan.customer, 'meta'):
                try:
                    points_earned = int(invoice.total_amount / 100)
                    if points_earned > 0:
                        challan.customer.meta.loyalty_points += points_earned
                        challan.customer.meta.save(update_fields=['loyalty_points'])
                except Exception:
                    pass

            # Update customer balance
            if invoice.customer_id:
                Customer.objects.filter(pk=invoice.customer_id).update(
                    current_balance=F('current_balance') + invoice.total_amount
                )

            # Rebuild general ledger entries
            from .serializers import _rebuild_sales_invoice_ledger
            _rebuild_sales_invoice_ledger(invoice.id)


            from billing.sequence_service import sync_sequence_after_creation, get_tenant_full_prefix
            full_prefix = get_tenant_full_prefix(tenant, document_type='sales_invoice')
            sync_sequence_after_creation(tenant, 'sales_invoice', full_prefix, invoice.invoice_number)

            # Update Challan status
            challan.is_billed = True
            challan.status = 'billed'
            challan.converted_invoice = invoice
            challan.save(update_fields=['is_billed', 'status', 'converted_invoice'])


            # If linked to sales order, update sales order stage
            if challan.sales_order_id:
                so = SalesOrder.objects.filter(pk=challan.sales_order_id).first()
                if so:
                    all_so_items = list(SalesOrderItem.objects.filter(order=so))
                    if all(i.is_fulfilled for i in all_so_items):
                        so.stage = 'completed'
                    else:
                        so.stage = 'shipped'
                    so.save(update_fields=['stage'])

        return Response({
            "message": "Delivery Challan converted to Invoice successfully",
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
        })
    except Exception as e:
        logger.error(f"Error converting delivery challan {pk} to invoice: {e}", exc_info=True)
        return Response({"message": f"Failed to convert: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_convert_challans_to_invoice(request):
    """
    Odoo-style consolidation:
    Converts multiple selected Delivery Challans into a single consolidated Sales Invoice.
    Aggregates all line items, joins delivery challan references, and ensures no double stock deduction.
    """
    tenant = request.user.active_tenant
    challan_ids = request.data.get('challan_ids', [])
    if not challan_ids or not isinstance(challan_ids, list):
        return Response({"message": "Please select at least one delivery challan to convert."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            challans = list(
                DeliveryChallan.objects.select_for_update(of=('self',))
                .select_related('customer', 'warehouse', 'sales_order')
                .prefetch_related('items__product', 'items__batch')
                .filter(pk__in=challan_ids, created_by=tenant)
            )

            if len(challans) != len(challan_ids):
                return Response({"message": "One or more delivery challans could not be found."}, status=status.HTTP_404_NOT_FOUND)

            already_billed = [c.challan_number for c in challans if c.is_billed]
            if already_billed:
                return Response(
                    {"message": f"Challan(s) {', '.join(already_billed)} have already been converted to an invoice."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Ensure all challans belong to the same customer
            customers = set(c.customer_id for c in challans)
            if len(customers) > 1:
                return Response(
                    {"message": "Cannot convert challans from different customers into a single invoice. Please select challans for the same customer."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            primary_challan = challans[0]
            customer = primary_challan.customer

            # Check credit limit
            est_total = sum((c.total_amount for c in challans), Decimal('0.00'))
            if customer and not customer.allow_credit:
                curr_balance = customer.current_balance or Decimal('0.00')
                cred_limit = customer.credit_limit or Decimal('0.00')
                new_balance = curr_balance + est_total
                if new_balance > cred_limit:
                    return Response(
                        {"message": f"Credit limit exceeded. Current: {curr_balance}, Limit: {cred_limit}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            from billing.sequence_service import allocate_next_number
            next_invoice_number = allocate_next_number(tenant, document_type='sales_invoice')

            all_challan_numbers = ", ".join(c.challan_number for c in challans)
            latest_challan_date = max(c.date for c in challans)
            combined_round_off = sum((c.round_off or Decimal('0.00') for c in challans), Decimal('0.00'))

            invoice = SalesInvoice(
                customer=customer,
                customer_name=customer.name if customer else (primary_challan.customer_name or ''),
                customer_address=primary_challan.customer_address or (customer.address if customer else ''),
                place_of_supply=customer.state if customer and customer.state else None,
                invoice_number=next_invoice_number,
                invoice_date=date.today(),
                challan_number=all_challan_numbers,
                challan_date=latest_challan_date,
                delivery_address=primary_challan.delivery_address,
                po_number=primary_challan.po_number or (primary_challan.sales_order.order_number if primary_challan.sales_order else ''),
                po_date=primary_challan.po_date,
                warehouse=primary_challan.warehouse,
                round_off=combined_round_off.quantize(Decimal('0.01')),
                total_amount=Decimal('0.00'),
                status='final',
                created_by=tenant,
            )
            invoice._skip_stock_deduction = True
            invoice.save()

            total_items_amount = Decimal('0.00')
            for challan in challans:
                for c_item in challan.items.all():
                    is_note = getattr(c_item, 'row_type', 'item') == 'note' or not c_item.product_id
                    prod = c_item.product
                    if is_note:
                        price = Decimal('0.00')
                        qty = 0
                        discount = Decimal('0.00')
                        tax = Decimal('0.00')
                        line_amount = Decimal('0.00')
                        unit = ''
                        hsn_sac_code = ''
                    else:
                        price = Decimal(str(
                            c_item.price if c_item.price is not None
                            else (getattr(prod, 'sale_price', None) or getattr(prod, 'price', None) or Decimal('0.00'))
                        ))
                        qty = int(c_item.quantity or 1)
                        discount = Decimal(str(c_item.discount or 0))
                        tax = Decimal(str(c_item.tax or 0))
                        base_amount = qty * price
                        discount_amount = (base_amount * discount) / Decimal('100')
                        taxable_amount = base_amount - discount_amount
                        tax_amount = (taxable_amount * tax) / Decimal('100')
                        line_amount = (taxable_amount + tax_amount).quantize(Decimal('0.01'))
                        total_items_amount += line_amount
                        unit = c_item.unit or getattr(prod, 'unit', 'pcs') or 'pcs'
                        hsn_sac_code = c_item.hsn_sac_code or getattr(prod, 'hsn_sac_code', '') or ''

                    inv_item = SalesInvoiceItem(
                        sales_invoice=invoice,
                        product=prod,
                        row_type=getattr(c_item, 'row_type', 'item') or 'item',
                        batch=c_item.batch,
                        hsn_sac_code=hsn_sac_code,
                        quantity=qty,
                        free_quantity=c_item.free_quantity or 0,
                        price=price,
                        amount=line_amount,
                        unit=unit,
                        discount=discount,
                        tax=tax,
                        description=getattr(c_item, 'description', '') or '',
                        source_item_id=str(c_item.id),
                    )
                    inv_item._skip_stock_deduction = True
                    inv_item.save()

            final_total = (total_items_amount + combined_round_off).quantize(Decimal('0.01'))
            invoice.total_amount = final_total
            invoice.save(update_fields=['total_amount'])

            TransactionMeta.objects.get_or_create(invoice=invoice)

            if customer:
                Customer.objects.filter(pk=customer.pk).update(
                    current_balance=F('current_balance') + final_total
                )

            from .serializers import _rebuild_sales_invoice_ledger
            _rebuild_sales_invoice_ledger(invoice.id)

            from billing.sequence_service import sync_sequence_after_creation, get_tenant_full_prefix
            full_prefix = get_tenant_full_prefix(tenant, document_type='sales_invoice')
            sync_sequence_after_creation(tenant, 'sales_invoice', full_prefix, invoice.invoice_number)

            for challan in challans:
                challan.is_billed = True
                challan.status = 'billed'
                challan.converted_invoice = invoice
                challan.save(update_fields=['is_billed', 'status', 'converted_invoice'])

                if challan.sales_order_id:
                    so = SalesOrder.objects.filter(pk=challan.sales_order_id).first()
                    if so:
                        all_so_items = list(SalesOrderItem.objects.filter(order=so))
                        if all(i.is_fulfilled for i in all_so_items):
                            so.stage = 'completed'
                        else:
                            so.stage = 'shipped'
                        so.save(update_fields=['stage'])

        return Response({
            "message": f"Successfully converted {len(challans)} Delivery Challans to Invoice #{invoice.invoice_number}",
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
        })
    except Exception as e:
        logger.error(f"Error bulk converting challans to invoice: {e}", exc_info=True)
        return Response({"message": f"Failed to convert: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def delivery_challan_pdf_download(request, pk):
    from django.http import HttpResponse
    from django.db.models import Q

    tenant = request.user.active_tenant
    try:
        challan = DeliveryChallan.objects.select_related('customer', 'warehouse').prefetch_related('items__product').get(
            Q(pk=pk) & (Q(created_by=tenant) | Q(created_by__parent=tenant))
        )
    except DeliveryChallan.DoesNotExist:
        return Response({'error': 'Delivery Challan not found.'}, status=status.HTTP_404_NOT_FOUND)

    # If client passed rendered HTML of the exact template preview, generate pixel-perfect vector PDF
    if request.method == 'POST' and isinstance(request.data, dict) and request.data.get('html'):
        try:
            from billing.html_pdf_service import render_html_to_vector_pdf
            pdf_bytes = render_html_to_vector_pdf(request.data['html'])
            filename = f"delivery-challan-{challan.challan_number or challan.id}.pdf"
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_bytes)
            return response
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"HTML vector PDF rendering failed: {e}")
            return Response(
                {'error': f'Server vector PDF rendering unavailable: {str(e)}', 'fallback_client': True},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

    template_data = None
    if request.method == 'POST':
        template_data = request.data.get('template') if (isinstance(request.data, dict) and 'template' in request.data) else request.data
    elif request.GET.get('primary_color'):
        template_data = {
            'colors': {
                'primary': request.GET.get('primary_color'),
                'secondary': request.GET.get('secondary_color'),
                'tableHeader': request.GET.get('table_header'),
                'tableBorder': request.GET.get('table_border'),
                'totalRow': request.GET.get('total_row'),
                'totalText': request.GET.get('total_text'),
            },
            'layoutType': request.GET.get('layout_type', 'classic'),
        }

    from billing.invoice_pdf_service import generate_invoice_pdf
    pdf_bytes = generate_invoice_pdf(
        invoice_obj=challan,
        tenant=tenant,
        document_type='delivery_challan',
        template_data=template_data,
    )

    filename = f"delivery-challan-{challan.challan_number or challan.id}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = len(pdf_bytes)
    return response

# =============================================================================
# INVOICE SETTINGS VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def invoice_settings_view(request):
    tenant = request.user.active_tenant
    try:
        settings = InvoiceSettings.objects.get(user=tenant)
    except InvoiceSettings.DoesNotExist:
        settings = InvoiceSettings.objects.create(user=tenant)
        
    if request.method == 'GET':
        serializer = InvoiceSettingsSerializer(settings)
        return Response(serializer.data)
        
    elif request.method == 'POST':
        serializer = InvoiceSettingsSerializer(settings, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# =============================================================================
# QUOTATION VIEWS
# =============================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def quotation_list_create(request):
    tenant = request.user.active_tenant

    if request.method == 'GET':
        search = request.GET.get('search', '').strip()
        status_filter = request.GET.get('status', '').strip()

        qs = Quotation.objects.filter(created_by=tenant).prefetch_related('items__product').order_by('-quotation_date', '-created_at')

        if search:
            qs = qs.filter(Q(quotation_number__icontains=search) | Q(customer_name__icontains=search))
        if status_filter and status_filter != 'all':
            qs = qs.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            serializer = QuotationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = QuotationSerializer(qs, many=True)
        return Response(serializer.data)

    serializer = QuotationSerializer(data=request.data, context={'request': request})
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def quotation_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        quotation = Quotation.objects.select_related('customer', 'warehouse').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except Quotation.DoesNotExist:
        return Response({'message': 'Quotation not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response(QuotationSerializer(quotation).data)

    if request.method in ['PUT', 'PATCH']:
        serializer = QuotationSerializer(
            quotation,
            data=request.data,
            partial=(request.method == 'PATCH'),
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            if hasattr(quotation, '_prefetched_objects_cache'):
                quotation._prefetched_objects_cache.clear()
            quotation.refresh_from_db()
            return Response(QuotationSerializer(quotation).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    quotation.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quotation_next_number(request):
    from billing.sequence_service import preview_next_number

    tenant = request.user.active_tenant
    prefix = request.GET.get('prefix') or getattr(tenant, 'quotation_prefix', 'QT-') or 'QT-'
    is_draft = request.GET.get('is_draft', 'false').lower() in ('true', '1')

    next_number, suffix = preview_next_number(
        tenant=tenant,
        document_type='quotation',
        prefix=prefix,
        is_draft=is_draft,
    )

    return Response({
        'success': True,
        'next_number': next_number,
        'suffix': suffix,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def quotation_convert_to_sales_order(request, pk):
    tenant = request.user.active_tenant
    try:
        quotation = Quotation.objects.select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except Quotation.DoesNotExist:
        return Response({'message': 'Quotation not found'}, status=status.HTTP_404_NOT_FOUND)

    if quotation.status not in ['approved', 'partially_converted']:
        return Response(
            {'message': 'Only approved quotations can be converted to sales orders.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    items_input = request.data.get('items', [])
    approved_item_ids = request.data.get('approved_item_ids', [])

    items_override_map = {}
    if isinstance(items_input, list) and items_input:
        for it in items_input:
            if isinstance(it, dict) and 'id' in it:
                items_override_map[str(it['id'])] = it
        target_ids = list(items_override_map.keys())
    elif approved_item_ids:
        target_ids = [str(i) for i in approved_item_ids]
    else:
        target_ids = []

    selected_qs = quotation.items.select_related('product').filter(approval_status='approved', converted_to_order=False)
    if target_ids:
        selected_qs = selected_qs.filter(id__in=target_ids)

    selected_items = list(selected_qs)
    if not selected_items:
        return Response(
            {'message': 'No approved quotation items selected for conversion.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from billing.sequence_service import allocate_next_number
    order_number = allocate_next_number(tenant, document_type='sales_order')

    order_customer = quotation.customer
    if not order_customer:
        from .models import Customer
        order_customer = Customer.objects.filter(name__iexact=quotation.customer_name, created_by=tenant).first()
        if not order_customer and quotation.customer_name:
            order_customer = Customer.objects.create(
                name=quotation.customer_name,
                address=quotation.customer_address,
                created_by=tenant,
            )

    if not order_customer:
        return Response({'message': 'Quotation must have a customer to convert.'}, status=status.HTTP_400_BAD_REQUEST)

    po_number = (request.data.get('po_number') or quotation.po_number or '').strip() or None
    po_date = request.data.get('po_date') or quotation.po_date or None

    order = SalesOrder.objects.create(
        order_number=order_number,
        date=date.today(),
        customer=order_customer,
        total_amount=Decimal('0.00'),
        po_number=po_number,
        po_date=po_date,
        notes=f'Converted from quotation {quotation.quotation_number}',
        source_quotation=quotation,
        created_by=tenant,
    )

    from billing.sync_service import DocumentSyncService
    total_amount = Decimal('0.00')

    for item in selected_items:
        override = items_override_map.get(str(item.id), {})

        raw_qty = override.get('quantity')
        qty = Decimal(str(raw_qty)) if raw_qty is not None and str(raw_qty).strip() != '' else item.quantity
        if qty <= 0:
            qty = item.quantity

        raw_disc = override.get('discount')
        discount = Decimal(str(raw_disc)) if raw_disc is not None and str(raw_disc).strip() != '' else (getattr(item, 'discount', Decimal('0.00')) or Decimal('0.00'))

        raw_price = override.get('price')
        price = Decimal(str(raw_price)) if raw_price is not None and str(raw_price).strip() != '' else item.price

        tax = getattr(item, 'tax', Decimal('0.00')) or Decimal('0.00')

        line_amount = DocumentSyncService.calculate_line_amount(
            quantity=qty,
            price=price,
            discount=discount,
            tax=tax
        )
        total_amount += line_amount

        SalesOrderItem.objects.create(
            order=order,
            product=item.product,
            quantity=qty,
            free_quantity=getattr(item, 'free_quantity', 0) or 0,
            price=price,
            amount=line_amount,
            unit=item.unit or (item.product.unit if item.product else 'pcs') or 'pcs',
            discount=discount,
            tax=tax,
            description=getattr(item, 'description', '') or '',
            source_item_id=str(item.id),
        )
        item.converted_to_order = True
        item.save(update_fields=['converted_to_order'])

    order.total_amount = total_amount
    order.save(update_fields=['total_amount'])

    remaining = quotation.items.filter(approval_status='approved', converted_to_order=False).exists()
    quotation.status = 'partially_converted' if remaining else 'converted'
    quotation.save(update_fields=['status'])

    return Response({
        'message': 'Quotation converted to sales order successfully.',
        'sales_order_id': str(order.id),
        'sales_order_number': order.order_number,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def quotation_pdf_download(request, pk):
    from django.http import HttpResponse
    from django.db.models import Q
    from billing.models_sidecar import Quotation

    tenant = request.user.active_tenant
    try:
        quotation = Quotation.objects.select_related('customer').prefetch_related('items__product').get(
            Q(pk=pk) & (Q(created_by=tenant) | Q(created_by__parent=tenant))
        )
    except Quotation.DoesNotExist:
        return Response({'error': 'Quotation not found.'}, status=status.HTTP_404_NOT_FOUND)

    # If client passed rendered HTML of the exact template preview, generate pixel-perfect vector PDF
    if request.method == 'POST' and isinstance(request.data, dict) and request.data.get('html'):
        try:
            from billing.html_pdf_service import render_html_to_vector_pdf
            pdf_bytes = render_html_to_vector_pdf(request.data['html'])
            filename = f"quotation-{quotation.quotation_number or quotation.id}.pdf"
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_bytes)
            return response
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"HTML vector PDF rendering failed: {e}")
            return Response(
                {'error': f'Server vector PDF rendering unavailable: {str(e)}', 'fallback_client': True},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

    template_data = None
    if request.method == 'POST':
        template_data = request.data.get('template') if (isinstance(request.data, dict) and 'template' in request.data) else request.data
    elif request.GET.get('primary_color'):
        template_data = {
            'colors': {
                'primary': request.GET.get('primary_color'),
                'secondary': request.GET.get('secondary_color'),
                'tableHeader': request.GET.get('table_header'),
                'tableBorder': request.GET.get('table_border'),
                'totalRow': request.GET.get('total_row'),
                'totalText': request.GET.get('total_text'),
            },
            'layoutType': request.GET.get('layout_type', 'classic'),
        }

    from billing.invoice_pdf_service import generate_invoice_pdf
    pdf_bytes = generate_invoice_pdf(
        invoice_obj=quotation,
        tenant=tenant,
        document_type='quotation',
        template_data=template_data,
    )

    filename = f"quotation-{quotation.quotation_number or quotation.id}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = len(pdf_bytes)
    return response


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sales_order_pdf_download(request, pk):
    from django.http import HttpResponse
    from django.db.models import Q
    from billing.models_sidecar import SalesOrder

    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.select_related('customer').prefetch_related('items__product').get(
            Q(pk=pk) & (Q(created_by=tenant) | Q(created_by__parent=tenant))
        )
    except SalesOrder.DoesNotExist:
        return Response({'error': 'Sales order not found.'}, status=status.HTTP_404_NOT_FOUND)

    # If client passed rendered HTML of the exact template preview, generate pixel-perfect vector PDF
    if request.method == 'POST' and isinstance(request.data, dict) and request.data.get('html'):
        try:
            from billing.html_pdf_service import render_html_to_vector_pdf
            pdf_bytes = render_html_to_vector_pdf(request.data['html'])
            filename = f"proforma-invoice-{order.order_number or order.id}.pdf"
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            response['Content-Length'] = len(pdf_bytes)
            return response
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"HTML vector PDF rendering failed: {e}")
            return Response(
                {'error': f'Server vector PDF rendering unavailable: {str(e)}', 'fallback_client': True},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

    template_data = None
    if request.method == 'POST':
        template_data = request.data.get('template') if (isinstance(request.data, dict) and 'template' in request.data) else request.data
    elif request.GET.get('primary_color'):
        template_data = {
            'colors': {
                'primary': request.GET.get('primary_color'),
                'secondary': request.GET.get('secondary_color'),
                'tableHeader': request.GET.get('table_header'),
                'tableBorder': request.GET.get('table_border'),
                'totalRow': request.GET.get('total_row'),
                'totalText': request.GET.get('total_text'),
            },
            'layoutType': request.GET.get('layout_type', 'classic'),
        }

    from billing.invoice_pdf_service import generate_invoice_pdf
    pdf_bytes = generate_invoice_pdf(
        invoice_obj=order,
        tenant=tenant,
        document_type='sales_order',
        template_data=template_data,
    )

    filename = f"proforma-invoice-{order.order_number or order.id}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = len(pdf_bytes)
    return response


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def convert_order_to_purchase_order(request, pk):
    """
    Converts a SalesOrder to a vendor PurchaseOrder (drop-shipping / procurement).
    Links items via source_item_id.
    """
    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesOrder.DoesNotExist:
        return Response({"message": "Sales order not found."}, status=status.HTTP_404_NOT_FOUND)

    vendor_id = request.data.get('vendor_id')
    vendor = None
    if vendor_id:
        from billing.models import Vendor
        vendor = Vendor.objects.filter(pk=vendor_id, created_by=tenant).first()

    from billing.sequence_service import allocate_next_number
    next_po_number = allocate_next_number(tenant, document_type='purchase_order', prefix='PO-')

    from billing.models import PurchaseOrder, PurchaseOrderItem
    po = PurchaseOrder.objects.create(
        po_number=next_po_number,
        vendor=vendor,
        expected_date=request.data.get('expected_date') or None,
        notes=request.data.get('notes') or f"Generated from Sales Order {order.order_number}",
        source_sales_order=order,
        total_amount=Decimal('0.00'),
        created_by=tenant,
    )

    total = Decimal('0.00')
    for so_item in order.items.all():
        if not so_item.product:
            continue
        cost_price = getattr(so_item.product, 'price', None) or getattr(so_item.product, 'cost_price', None) or so_item.price or Decimal('0.00')
        line_total = Decimal(str(so_item.quantity)) * Decimal(str(cost_price))
        total += line_total
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            product=so_item.product,
            quantity=so_item.quantity,
            unit=so_item.unit or so_item.product.unit or 'pcs',
            price=cost_price,
            discount=Decimal('0.00'),
            tax=so_item.tax or Decimal('0.00'),
            amount=line_total,
            description=so_item.description or '',
            source_item_id=str(so_item.id),
        )

    po.total_amount = total
    po.save(update_fields=['total_amount'])

    return Response({
        "message": "Purchase order created from sales order successfully.",
        "purchase_order_id": str(po.id),
        "po_number": po.po_number,
    }, status=status.HTTP_201_CREATED)




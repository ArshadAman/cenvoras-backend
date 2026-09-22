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

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def sales_order_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        order = SalesOrder.objects.select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
    except SalesOrder.DoesNotExist:
        return Response({"success": False, "message": "Order not found"}, status=404)
        
    if request.method == 'GET':
        serializer = SalesOrderSerializer(order)
        return Response(serializer.data)
        
    elif request.method == 'PUT':
        serializer = SalesOrderSerializer(order, data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
    elif request.method == 'DELETE':
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

    if order.stage == 'completed':
        return Response({"message": "Order has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # Lock order row for update
        order = SalesOrder.objects.select_for_update().select_related('customer').prefetch_related('items__product').get(pk=pk, created_by=tenant)
        if order.stage == 'completed':
            return Response({"message": "Order has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

        # Check credit limit if allow_credit is disabled
        if order.customer and not order.customer.allow_credit:
            new_balance = order.customer.current_balance + order.total_amount
            if new_balance > order.customer.credit_limit:
                return Response(
                    {"message": f"Credit limit exceeded. Current: {order.customer.current_balance}, Limit: {order.customer.credit_limit}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        from billing.sequence_service import allocate_next_number
        next_invoice_number = allocate_next_number(tenant, document_type='sales_invoice')

        invoice = SalesInvoice.objects.create(
            customer=order.customer,
            customer_name=order.customer.name if order.customer else '',
            customer_address=order.customer.address if order.customer else '',
            place_of_supply=order.customer.state if order.customer and order.customer.state else None,
            invoice_number=next_invoice_number,
            invoice_date=date.today(),
            created_by=tenant,
            total_amount=Decimal('0.00'),
            status='final',
        )

        raw_items = []
        for item in order.items.all():
            item_unit = getattr(item, 'unit', None) or (item.product.unit if item.product else 'pcs') or 'pcs'
            item_tax = getattr(item, 'tax', None) if getattr(item, 'tax', None) is not None else (item.product.tax if item.product else Decimal('0.00'))
            item_discount = getattr(item, 'discount', Decimal('0.00')) or Decimal('0.00')
            item_free_qty = getattr(item, 'free_quantity', 0) or 0
            raw_items.append({
                'product': item.product,
                'quantity': item.quantity,
                'price': item.price,
                'amount': item.amount,
                'unit': item_unit,
                'tax': item_tax,
                'discount': item_discount,
                'free_quantity': item_free_qty,
            })

        # Evaluate active schemes if any items have free_quantity == 0
        bonus_items = []
        try:
            from billing.scheme_service import evaluate_schemes_for_items
            scheme_res = evaluate_schemes_for_items(tenant=tenant, items=raw_items, evaluation_date=invoice.invoice_date)
            raw_items = scheme_res.get('items', raw_items)
            bonus_items = scheme_res.get('additional_free_items', [])
        except Exception as e:
            pass

        for r_item in raw_items:
            SalesInvoiceItem.objects.create(
                sales_invoice=invoice,
                product=r_item['product'],
                quantity=r_item['quantity'],
                free_quantity=r_item.get('free_quantity', 0),
                price=r_item['price'],
                amount=r_item['amount'],
                unit=r_item['unit'],
                tax=r_item['tax'],
                discount=r_item['discount'],
            )

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

        # Update Order Stage
        order.stage = 'completed'
        order.save(update_fields=['stage'])

    return Response({"message": "Converted successfully", "invoice_id": invoice.id})

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

        challans = DeliveryChallan.objects.filter(created_by=tenant).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')
        
        if search:
            challans = challans.filter(
                Q(challan_number__icontains=search) | 
                Q(customer__name__icontains=search) |
                Q(customer_name__icontains=search)
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

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def delivery_challan_detail(request, pk):
    tenant = request.user.active_tenant
    try:
        challan = DeliveryChallan.objects.select_related('customer', 'warehouse', 'converted_invoice').prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
    except DeliveryChallan.DoesNotExist:
        return Response({"message": "Delivery Challan not found"}, status=status.HTTP_404_NOT_FOUND)
        
    if request.method == 'GET':
        serializer = DeliveryChallanSerializer(challan)
        return Response(serializer.data)
    elif request.method == 'PUT':
        serializer = DeliveryChallanSerializer(challan, data=request.data, context={'request': request})
        if serializer.is_valid():
            updated_challan = serializer.save()
            return Response(DeliveryChallanSerializer(updated_challan).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    elif request.method == 'DELETE':
        if challan.is_billed:
            return Response({"message": "Cannot delete an invoiced delivery challan."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Restore stock for deleted challan
        from inventory.models import StockPoint
        from django.db.models import F
        target_warehouse = challan.warehouse
        for item in challan.items.all():
            eff_qty = (item.quantity or 0) + (item.free_quantity or 0)
            if eff_qty > 0:
                Product.objects.filter(pk=item.product_id).update(stock=F('stock') + eff_qty)
                if item.batch and target_warehouse:
                    StockPoint.objects.filter(batch=item.batch, warehouse=target_warehouse).update(
                        quantity=F('quantity') + eff_qty
                    )
        challan.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def delivery_challan_next_number(request):
    from billing.sequence_service import preview_next_number

    tenant = request.user.active_tenant
    prefix = request.GET.get('prefix', 'DC-')
    tenant_code = str(tenant.id)[:4].upper()

    next_number, suffix = preview_next_number(
        tenant=tenant,
        document_type='delivery_challan',
        prefix=prefix,
    )

    return Response({
        'success': True,
        'uuid_prefix': tenant_code,
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

        from billing.sequence_service import allocate_next_number
        next_challan_number = allocate_next_number(tenant, document_type='delivery_challan', prefix='DC-')

        challan = DeliveryChallan.objects.create(
            challan_number=next_challan_number,
            date=date.today(),
            customer=order.customer,
            customer_name=order.customer.name if order.customer else '',
            customer_address=order.customer.address if order.customer else '',
            delivery_address=order.customer.address if order.customer else '',
            sales_order=order,
            total_amount=Decimal('0.00'),
            status='open',
            notes=f"Converted from Sales Order {order.order_number}",
            created_by=tenant,
        )

        total_amount = Decimal('0.00')
        from django.db.models import F

        for item in order.items.all():
            item_unit = getattr(item, 'unit', None) or (item.product.unit if item.product else 'pcs') or 'pcs'
            item_tax = getattr(item, 'tax', None) if getattr(item, 'tax', None) is not None else (item.product.tax if item.product else Decimal('0.00'))
            item_discount = getattr(item, 'discount', Decimal('0.00')) or Decimal('0.00')
            item_free_qty = getattr(item, 'free_quantity', 0) or 0
            
            base_amt = Decimal(str(item.quantity)) * Decimal(str(item.price))
            disc_amt = (base_amt * Decimal(str(item_discount))) / Decimal('100')
            taxable = base_amt - disc_amt
            tax_amt = (taxable * Decimal(str(item_tax))) / Decimal('100')
            line_amount = (taxable + tax_amt).quantize(Decimal('0.01'))
            total_amount += line_amount

            DeliveryChallanItem.objects.create(
                challan=challan,
                product=item.product,
                quantity=item.quantity,
                free_quantity=item_free_qty,
                price=item.price,
                amount=line_amount,
                unit=item_unit,
                tax=item_tax,
                discount=item_discount,
            )

            # Deduct stock for dispatched item
            eff_qty = (item.quantity or 0) + item_free_qty
            if eff_qty > 0:
                Product.objects.filter(pk=item.product_id).update(stock=F('stock') - eff_qty)

        challan.total_amount = total_amount
        challan.save(update_fields=['total_amount'])

        # Update order stage
        order.stage = 'challan_created'
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
        challan = DeliveryChallan.objects.select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
    except DeliveryChallan.DoesNotExist:
        return Response({"message": "Delivery Challan not found"}, status=status.HTTP_404_NOT_FOUND)

    if challan.is_billed:
        return Response({"message": "Delivery Challan has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        challan = DeliveryChallan.objects.select_for_update().select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch').get(pk=pk, created_by=tenant)
        if challan.is_billed:
            return Response({"message": "Delivery Challan has already been converted to an invoice."}, status=status.HTTP_400_BAD_REQUEST)

        # Check credit limit
        if challan.customer and not challan.customer.allow_credit:
            new_balance = challan.customer.current_balance + challan.total_amount
            if new_balance > challan.customer.credit_limit:
                return Response(
                    {"message": f"Credit limit exceeded. Current: {challan.customer.current_balance}, Limit: {challan.customer.credit_limit}"},
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
            po_number=challan.po_number,
            po_date=challan.po_date,
            warehouse=challan.warehouse,
            round_off=challan.round_off or Decimal('0.00'),
            total_amount=challan.total_amount,
            status='final',
            created_by=tenant,
        )
        # CRUCIAL: Set flag so signal does NOT deduct stock a second time!
        invoice._skip_stock_deduction = True
        invoice.save()

        for c_item in challan.items.all():
            inv_item = SalesInvoiceItem(
                sales_invoice=invoice,
                product=c_item.product,
                batch=c_item.batch,
                hsn_sac_code=c_item.hsn_sac_code,
                quantity=c_item.quantity,
                free_quantity=c_item.free_quantity,
                price=c_item.price,
                amount=c_item.amount,
                unit=c_item.unit or 'pcs',
                discount=c_item.discount,
                tax=c_item.tax,
            )
            inv_item._skip_stock_deduction = True
            inv_item.save()

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

        # Update Challan status
        challan.is_billed = True
        challan.status = 'billed'
        challan.converted_invoice = invoice
        challan.save(update_fields=['is_billed', 'status', 'converted_invoice'])

        # If linked to sales order, mark sales order completed
        if challan.sales_order:
            challan.sales_order.stage = 'completed'
            challan.sales_order.save(update_fields=['stage'])

    return Response({
        "message": "Delivery Challan converted to Invoice successfully",
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
    })


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

    template_data = None
    if request.method == 'POST':
        template_data = request.data.get('template')
    elif request.GET.get('primary_color'):
        template_data = {
            'colors': {
                'primary': request.GET.get('primary_color'),
                'secondary': request.GET.get('secondary_color'),
                'tableHeader': request.GET.get('table_header'),
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
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    quotation.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quotation_next_number(request):
    from billing.sequence_service import preview_next_number

    tenant = request.user.active_tenant
    prefix = request.GET.get('prefix', 'QT-')
    tenant_code = str(tenant.id)[:4].upper()

    next_number, suffix = preview_next_number(
        tenant=tenant,
        document_type='quotation',
        prefix=prefix,
    )

    return Response({
        'success': True,
        'uuid_prefix': tenant_code,
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

    approved_item_ids = request.data.get('approved_item_ids', [])
    selected_qs = quotation.items.select_related('product').filter(approval_status='approved', converted_to_order=False)
    if approved_item_ids:
        selected_qs = selected_qs.filter(id__in=approved_item_ids)

    selected_items = list(selected_qs)
    if not selected_items:
        return Response(
            {'message': 'No approved quotation items selected for conversion.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    order_total = sum(Decimal(str(item.amount)) for item in selected_items)
    next_index = SalesOrder.objects.filter(created_by=tenant).count() + 1
    order_number = f'SO-{tenant.id.hex[:4].upper()}-{next_index:03d}'

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

    order = SalesOrder.objects.create(
        order_number=order_number,
        date=date.today(),
        customer=order_customer,
        total_amount=order_total,
        notes=f'Converted from quotation {quotation.quotation_number}',
        created_by=tenant,
    )

    for item in selected_items:
        SalesOrderItem.objects.create(
            order=order,
            product=item.product,
            quantity=item.quantity,
            free_quantity=getattr(item, 'free_quantity', 0) or 0,
            price=item.price,
            amount=item.amount,
            unit=item.unit or (item.product.unit if item.product else 'pcs') or 'pcs',
            discount=getattr(item, 'discount', Decimal('0.00')) or Decimal('0.00'),
            tax=getattr(item, 'tax', Decimal('0.00')) or Decimal('0.00'),
        )
        item.converted_to_order = True
        item.save(update_fields=['converted_to_order'])

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

    template_data = None
    if request.method == 'POST':
        template_data = request.data.get('template')
    elif request.GET.get('primary_color'):
        template_data = {
            'colors': {
                'primary': request.GET.get('primary_color'),
                'secondary': request.GET.get('secondary_color'),
                'tableHeader': request.GET.get('table_header'),
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


import logging
from decimal import Decimal
import threading
from django.db import transaction
from django.db.models import F

logger = logging.getLogger(__name__)

# Thread-local storage for preventing recursion loops during cross-document synchronization
_sync_state = threading.local()

def _get_visited_set():
    if not hasattr(_sync_state, 'visited'):
        _sync_state.visited = set()
    return _sync_state.visited

def _doc_key(doc):
    return f"{doc.__class__.__name__}_{str(doc.pk)}"


class DocumentSyncService:
    """
    Centralized Bidirectional Synchronization Engine for billing documents:
    Quotation <--> SalesOrder <--> DeliveryChallan <--> SalesInvoice
    SalesOrder --> PurchaseOrder

    Propagates item alterations (quantity, price, discount, tax), additions,
    and deletions forward and backward through connected document trees.
    """

    @classmethod
    def calculate_line_amount(cls, quantity, price, discount=0, tax=0):
        qty = Decimal(str(quantity or 0))
        unit_price = Decimal(str(price or 0))
        disc_rate = Decimal(str(discount or 0))
        tax_rate = Decimal(str(tax or 0))

        base = qty * unit_price
        disc_amt = (base * disc_rate) / Decimal('100')
        taxable = base - disc_amt
        tax_amt = (taxable * tax_rate) / Decimal('100')
        return (taxable + tax_amt).quantize(Decimal('0.01'))

    @classmethod
    def recalculate_document_total(cls, document):
        """Recalculates total_amount from items and updates document."""
        if hasattr(document, '_prefetched_objects_cache'):
            document._prefetched_objects_cache.clear()

        items = list(document.items.all())
        total = sum(Decimal(str(item.amount or 0)) for item in items)
        round_off = Decimal(str(getattr(document, 'round_off', 0) or 0))
        document.total_amount = (total + round_off).quantize(Decimal('0.01'))
        
        # Save without triggering full clean/signals
        update_fields = ['total_amount']
        if hasattr(document, 'refresh_payment_status'):
            document.refresh_payment_status(save=False)
            update_fields.append('payment_status')
            
        document.save(update_fields=[f for f in update_fields if hasattr(document, f)])
        document.__class__.objects.filter(pk=document.pk).update(total_amount=document.total_amount)
        return document.total_amount

    @classmethod
    def get_connected_documents(cls, document):
        """
        Returns a dict of {'upstream': [...], 'downstream': [...]} documents
        connected to the given document.
        """
        from billing.models import SalesInvoice, PurchaseOrder
        from billing.models_sidecar import Quotation, SalesOrder, DeliveryChallan

        upstream = []
        downstream = []

        if isinstance(document, SalesInvoice):
            # Upstream: Delivery Challans converted into this invoice
            challans = list(document.source_delivery_challans.all())
            upstream.extend(challans)

            # Upstream: Direct Sales Order (if converted from SO directly or linked via challans)
            if getattr(document, 'source_sales_order', None):
                if document.source_sales_order not in upstream:
                    upstream.append(document.source_sales_order)
            elif document.po_number:
                so = SalesOrder.objects.filter(order_number=document.po_number, created_by=document.created_by).first()
                if so and so not in upstream:
                    upstream.append(so)

        elif isinstance(document, DeliveryChallan):
            # Upstream: Sales Order
            if getattr(document, 'sales_order', None):
                upstream.append(document.sales_order)

            # Downstream: Converted Sales Invoice
            if getattr(document, 'converted_invoice', None):
                downstream.append(document.converted_invoice)

        elif isinstance(document, SalesOrder):
            # Upstream: Quotation
            if getattr(document, 'source_quotation', None):
                upstream.append(document.source_quotation)

            # Downstream: Delivery Challans
            challans = list(document.deliverychallan_set.all())
            downstream.extend(challans)

            # Downstream: Invoices
            invoices = list(document.sales_invoices.all())
            for inv in invoices:
                if inv not in downstream:
                    downstream.append(inv)

            # Downstream: Purchase Orders
            pos = list(document.purchase_orders.all())
            downstream.extend(pos)

        elif isinstance(document, Quotation):
            # Downstream: Sales Orders
            orders = list(document.sales_orders.all())
            downstream.extend(orders)

        elif isinstance(document, PurchaseOrder):
            # Upstream: Sales Order
            if getattr(document, 'source_sales_order', None):
                upstream.append(document.source_sales_order)

        return {'upstream': upstream, 'downstream': downstream}

    @classmethod
    def sync_document_items(cls, document, incoming_items_data, user=None):
        """
        Updates document items using a diff algorithm (Update, Delete, Add)
        instead of blindly deleting and recreating, preserving row IDs and
        source_item_id relationships.
        Then propagates the changes across the entire connected document network.
        """
        visited = _get_visited_set()
        d_key = _doc_key(document)
        is_root = d_key not in visited

        if is_root:
            visited.clear()
            visited.add(d_key)

        from inventory.models import Product, ProductBatch

        if hasattr(document, '_prefetched_objects_cache'):
            document._prefetched_objects_cache.clear()

        existing_items = list(document.items.all())
        existing_by_id = {str(item.id): item for item in existing_items}
        existing_by_source_id = {str(item.source_item_id): item for item in existing_items if getattr(item, 'source_item_id', None)}

        matched_existing_ids = set()
        to_update = []  # list of (existing_item, new_data)
        to_create = []  # list of new_data

        for raw_entry in incoming_items_data:
            entry = dict(raw_entry)
            item_id = str(entry.get('id') or '') if entry.get('id') else None
            source_id = str(entry.get('source_item_id') or '') if entry.get('source_item_id') else None

            matched_item = None
            if item_id and item_id in existing_by_id and item_id not in matched_existing_ids:
                matched_item = existing_by_id[item_id]
            elif source_id and source_id in existing_by_source_id and str(existing_by_source_id[source_id].id) not in matched_existing_ids:
                matched_item = existing_by_source_id[source_id]
            elif entry.get('product'):
                prod_val = entry.get('product')
                prod_id = getattr(prod_val, 'id', None) or prod_val
                candidates = [
                    ei for ei in existing_items
                    if (ei.product_id == prod_id or str(ei.product_id) == str(prod_id))
                    and str(ei.id) not in matched_existing_ids
                ]
                if len(candidates) == 1:
                    matched_item = candidates[0]

            if matched_item:
                matched_existing_ids.add(str(matched_item.id))
                to_update.append((matched_item, entry))
            else:
                to_create.append(entry)

        to_delete = [item for item in existing_items if str(item.id) not in matched_existing_ids]

        updated_items = []
        deleted_items = []
        created_items = []

        # 1. Apply Updates
        for item, new_data in to_update:
            old_qty = getattr(item, 'quantity', 0)
            old_free_qty = getattr(item, 'free_quantity', 0)

            # Update scalar fields
            if 'quantity' in new_data:
                item.quantity = new_data['quantity']
            if 'free_quantity' in new_data and hasattr(item, 'free_quantity'):
                item.free_quantity = new_data['free_quantity']
            if 'price' in new_data:
                item.price = Decimal(str(new_data['price']))
            if 'discount' in new_data:
                item.discount = Decimal(str(new_data['discount']))
            if 'tax' in new_data:
                item.tax = Decimal(str(new_data['tax']))
            if 'unit' in new_data and new_data['unit'] is not None:
                item.unit = new_data['unit']
            if 'description' in new_data and new_data['description'] is not None:
                item.description = new_data['description']
            if 'hsn_sac_code' in new_data and hasattr(item, 'hsn_sac_code') and new_data['hsn_sac_code'] is not None:
                item.hsn_sac_code = new_data['hsn_sac_code']
            if 'source_item_id' in new_data and hasattr(item, 'source_item_id') and new_data['source_item_id']:
                item.source_item_id = str(new_data['source_item_id'])

            # Recalculate item line amount
            item.amount = cls.calculate_line_amount(
                quantity=item.quantity,
                price=item.price,
                discount=item.discount,
                tax=item.tax
            )

            # Stock adjustments if document manages stock directly (e.g. DeliveryChallan)
            from billing.models_sidecar import DeliveryChallan
            if isinstance(document, DeliveryChallan) and item.product_id:
                new_qty = (item.quantity or 0) + (getattr(item, 'free_quantity', 0) or 0)
                delta = new_qty - (old_qty + old_free_qty)
                if delta != 0:
                    Product.objects.filter(pk=item.product_id).update(stock=F('stock') - delta)
                    if getattr(item, 'batch_id', None) and getattr(document, 'warehouse_id', None):
                        from inventory.models import StockPoint
                        sp, _ = StockPoint.objects.get_or_create(
                            batch_id=item.batch_id, warehouse_id=document.warehouse_id, defaults={'quantity': 0}
                        )
                        StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - delta)

            item.save()
            updated_items.append(item)

        # 2. Apply Deletions
        for item in to_delete:
            from billing.models_sidecar import DeliveryChallan
            if isinstance(document, DeliveryChallan) and item.product_id:
                restore_qty = (item.quantity or 0) + (getattr(item, 'free_quantity', 0) or 0)
                if restore_qty > 0:
                    Product.objects.filter(pk=item.product_id).update(stock=F('stock') + restore_qty)
                    if getattr(item, 'batch_id', None) and getattr(document, 'warehouse_id', None):
                        from inventory.models import StockPoint
                        sp, _ = StockPoint.objects.get_or_create(
                            batch_id=item.batch_id, warehouse_id=document.warehouse_id, defaults={'quantity': 0}
                        )
                        StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') + restore_qty)

            deleted_items.append({
                'id': str(item.id),
                'source_item_id': str(getattr(item, 'source_item_id', '')) or None,
                'product_id': item.product_id if getattr(item, 'product_id', None) else None,
            })
            item.delete()

        # 3. Apply Creations
        ItemModel = document.items.model
        for new_data in to_create:
            clean_data = dict(new_data)
            clean_data.pop('id', None)
            clean_data.pop('applied_scheme', None)
            clean_data.pop('product_detail', None)
            clean_data.pop('is_scheme_bonus', None)

            # Resolve product
            prod_val = clean_data.get('product')
            if isinstance(prod_val, str) and prod_val.strip():
                try:
                    import uuid
                    uuid_obj = uuid.UUID(prod_val)
                    prod_obj = Product.objects.filter(id=uuid_obj).first()
                    clean_data['product'] = prod_obj
                except ValueError:
                    tenant = getattr(document, 'created_by', user)
                    prod_obj = Product.objects.filter(name__iexact=prod_val, created_by=tenant).first()
                    clean_data['product'] = prod_obj

            is_note = clean_data.get('row_type') == 'note' or (not clean_data.get('product') and clean_data.get('description'))
            if is_note:
                clean_data['row_type'] = 'note'
                clean_data['product'] = None
                clean_data['quantity'] = 0
                clean_data['price'] = Decimal('0.00')
                clean_data['discount'] = Decimal('0.00')
                clean_data['tax'] = Decimal('0.00')
                clean_data['amount'] = Decimal('0.00')
            else:
                qty = clean_data.get('quantity', 1) or 1
                price = Decimal(str(clean_data.get('price', 0) or 0))
                discount = Decimal(str(clean_data.get('discount', 0) or 0))
                tax = Decimal(str(clean_data.get('tax', 0) or 0))
                clean_data['amount'] = cls.calculate_line_amount(qty, price, discount, tax)

            # Determine FK field name for document (order, challan, sales_invoice, etc.)
            fk_field = None
            for field in ItemModel._meta.fields:
                if field.is_relation and isinstance(document, field.related_model):
                    fk_field = field.name
                    break

            if fk_field:
                clean_data[fk_field] = document

            created_item = ItemModel.objects.create(**clean_data)

            from billing.models_sidecar import DeliveryChallan
            if isinstance(document, DeliveryChallan) and created_item.product_id:
                deduct_qty = (created_item.quantity or 0) + (getattr(created_item, 'free_quantity', 0) or 0)
                if deduct_qty > 0:
                    Product.objects.filter(pk=created_item.product_id).update(stock=F('stock') - deduct_qty)
                    if getattr(created_item, 'batch_id', None) and getattr(document, 'warehouse_id', None):
                        from inventory.models import StockPoint
                        sp, _ = StockPoint.objects.get_or_create(
                            batch_id=created_item.batch_id, warehouse_id=document.warehouse_id, defaults={'quantity': 0}
                        )
                        StockPoint.objects.filter(pk=sp.pk).update(quantity=F('quantity') - deduct_qty)

            created_items.append(created_item)

        # 4. Recalculate Document Total
        cls.recalculate_document_total(document)

        # 5. Propagate changes across connected documents
        cls._propagate_changes(document, updated_items, deleted_items, created_items)

        if is_root:
            visited.clear()

        return document

    @classmethod
    def _propagate_changes(cls, document, updated_items, deleted_items, created_items):
        """Recursively propagates row changes to upstream and downstream documents."""
        visited = _get_visited_set()
        connections = cls.get_connected_documents(document)

        for target in connections['upstream'] + connections['downstream']:
            t_key = _doc_key(target)
            if t_key in visited:
                continue
            visited.add(t_key)

            is_target_upstream = target in connections['upstream']
            cls._sync_target_document(document, target, updated_items, deleted_items, created_items, is_upstream=is_target_upstream)

    @classmethod
    def _sync_target_document(cls, source_doc, target_doc, updated_items, deleted_items, created_items, is_upstream=False):
        """Synchronizes item changes from source_doc into target_doc."""
        from inventory.models import Product
        from billing.models import PurchaseOrder
        from billing.models_sidecar import DeliveryChallan, SalesOrder
        target_items = list(target_doc.items.all())

        # 1. Propagate Updates
        for item in updated_items:
            matching_target_item = None
            if is_upstream:
                # Upstream target has item whose ID == item.source_item_id
                if getattr(item, 'source_item_id', None):
                    matching_target_item = next((ti for ti in target_items if str(ti.id) == str(item.source_item_id)), None)
            else:
                # Downstream target has item whose source_item_id == item.id
                matching_target_item = next((ti for ti in target_items if getattr(ti, 'source_item_id', None) == str(item.id)), None)

            # Fallback matching by product if source_item_id wasn't set yet
            if not matching_target_item and item.product_id:
                same_prod = [ti for ti in target_items if ti.product_id == item.product_id]
                if len(same_prod) == 1:
                    matching_target_item = same_prod[0]
                    # Link them up for future edits
                    if is_upstream and hasattr(item, 'source_item_id'):
                        item.source_item_id = str(matching_target_item.id)
                        item.save(update_fields=['source_item_id'])
                    elif not is_upstream and hasattr(matching_target_item, 'source_item_id'):
                        matching_target_item.source_item_id = str(item.id)
                        matching_target_item.save(update_fields=['source_item_id'])

            from billing.models import PurchaseOrder
            from billing.models_sidecar import SalesOrder
            is_po_sync = isinstance(target_doc, PurchaseOrder) or isinstance(source_doc, PurchaseOrder)
            is_dc_to_so_upstream = isinstance(source_doc, DeliveryChallan) and isinstance(target_doc, SalesOrder) and is_upstream

            if matching_target_item:
                old_t_qty = getattr(matching_target_item, 'quantity', 0)
                if is_dc_to_so_upstream:
                    # When editing a Delivery Challan linked to a Sales Order:
                    # Do NOT overwrite matching_target_item.quantity (the original ordered quantity)!
                    # Instead, adjust dispatched_quantity by delta = item.quantity - old_dispatched_for_this_challan
                    # matching_target_item.dispatched_quantity will be recalculated or adjusted.
                    pass
                else:
                    matching_target_item.quantity = item.quantity
                    if hasattr(matching_target_item, 'free_quantity') and hasattr(item, 'free_quantity'):
                        matching_target_item.free_quantity = item.free_quantity
                    if not is_po_sync:
                        matching_target_item.price = item.price
                        matching_target_item.discount = item.discount
                        matching_target_item.tax = item.tax
                    matching_target_item.amount = cls.calculate_line_amount(
                        matching_target_item.quantity,
                        matching_target_item.price,
                        matching_target_item.discount,
                        matching_target_item.tax
                    )
                    if hasattr(matching_target_item, 'unit') and hasattr(item, 'unit'):
                        matching_target_item.unit = item.unit
                    if hasattr(matching_target_item, 'description') and hasattr(item, 'description'):
                        matching_target_item.description = item.description

                    # Stock adjustment for DeliveryChallan target
                    if isinstance(target_doc, DeliveryChallan) and matching_target_item.product_id:
                        delta = matching_target_item.quantity - old_t_qty
                        if delta != 0:
                            Product.objects.filter(pk=matching_target_item.product_id).update(stock=F('stock') - delta)

                    matching_target_item.save()

        # 2. Propagate Deletions
        for del_info in deleted_items:
            del_id = del_info['id']
            del_source_id = del_info.get('source_item_id')
            del_prod_id = del_info.get('product_id')

            matching_target_item = None
            if is_upstream and del_source_id:
                matching_target_item = next((ti for ti in target_items if str(ti.id) == str(del_source_id)), None)
            elif not is_upstream:
                matching_target_item = next((ti for ti in target_items if getattr(ti, 'source_item_id', None) == str(del_id)), None)

            if not matching_target_item and del_prod_id:
                same_prod = [ti for ti in target_items if ti.product_id == del_prod_id]
                if len(same_prod) == 1:
                    matching_target_item = same_prod[0]

            if matching_target_item:
                from billing.models_sidecar import DeliveryChallan, SalesOrder
                is_dc_to_so_upstream = isinstance(source_doc, DeliveryChallan) and isinstance(target_doc, SalesOrder) and is_upstream
                if is_dc_to_so_upstream:
                    # Deleting an item from a challan does NOT delete the item from the sales order!
                    # The sales order line remains, but dispatched_quantity will be recomputed below.
                    pass
                else:
                    if isinstance(target_doc, DeliveryChallan) and matching_target_item.product_id:
                        restore_qty = (matching_target_item.quantity or 0) + (getattr(matching_target_item, 'free_quantity', 0) or 0)
                        if restore_qty > 0:
                            Product.objects.filter(pk=matching_target_item.product_id).update(stock=F('stock') + restore_qty)

                    matching_target_item.delete()

        # 3. Propagate Additions
        TargetItemModel = target_doc.items.model
        from billing.models_sidecar import DeliveryChallan, SalesOrder
        is_dc_to_so_upstream = isinstance(source_doc, DeliveryChallan) and isinstance(target_doc, SalesOrder) and is_upstream

        if not is_dc_to_so_upstream:
            for item in created_items:
                # Check if target already has this item
                already_exists = False
                if is_upstream:
                    already_exists = any(str(ti.id) == getattr(item, 'source_item_id', None) for ti in target_items)
                else:
                    already_exists = any(getattr(ti, 'source_item_id', None) == str(item.id) for ti in target_items)

                if not already_exists:
                    is_note = getattr(item, 'row_type', 'item') == 'note' or not item.product
                    if isinstance(target_doc, PurchaseOrder):
                        if is_note:
                            continue
                        cost_price = getattr(item.product, 'price', None) or getattr(item.product, 'cost_price', None) or item.price
                        item_price = cost_price
                        item_discount = Decimal('0.00')
                    else:
                        item_price = item.price
                        item_discount = item.discount

                    item_data = {
                        'product': item.product,
                        'quantity': 0 if is_note else item.quantity,
                        'price': Decimal('0.00') if is_note else item_price,
                        'discount': Decimal('0.00') if is_note else item_discount,
                        'tax': Decimal('0.00') if is_note else item.tax,
                        'amount': Decimal('0.00') if is_note else cls.calculate_line_amount(item.quantity, item_price, item_discount, item.tax),
                        'unit': getattr(item, 'unit', 'pcs') or 'pcs',
                        'description': getattr(item, 'description', '') or '',
                    }
                    if hasattr(TargetItemModel, 'free_quantity'):
                        item_data['free_quantity'] = getattr(item, 'free_quantity', 0) or 0
                    if hasattr(TargetItemModel, 'batch'):
                        item_data['batch'] = getattr(item, 'batch', None)
                    if hasattr(TargetItemModel, 'hsn_sac_code'):
                        item_data['hsn_sac_code'] = getattr(item, 'hsn_sac_code', '') or ''
                    if hasattr(TargetItemModel, 'row_type'):
                        item_data['row_type'] = getattr(item, 'row_type', 'item') or 'item'

                    if not is_upstream and hasattr(TargetItemModel, 'source_item_id'):
                        item_data['source_item_id'] = str(item.id)

                    fk_field = None
                    for field in TargetItemModel._meta.fields:
                        if field.is_relation and isinstance(target_doc, field.related_model):
                            fk_field = field.name
                            break

                    if fk_field:
                        item_data[fk_field] = target_doc

                    new_target_item = TargetItemModel.objects.create(**item_data)

                    # If we created upstream, link backward
                    if is_upstream and hasattr(item, 'source_item_id'):
                        item.source_item_id = str(new_target_item.id)
                        item.save(update_fields=['source_item_id'])

                    # Stock adjustment for DeliveryChallan target
                    from billing.models_sidecar import DeliveryChallan
                    if isinstance(target_doc, DeliveryChallan) and new_target_item.product_id:
                        deduct_qty = (new_target_item.quantity or 0) + (getattr(new_target_item, 'free_quantity', 0) or 0)
                        if deduct_qty > 0:
                            Product.objects.filter(pk=new_target_item.product_id).update(stock=F('stock') - deduct_qty)

        # If target is SalesOrder and source is DeliveryChallan, re-evaluate dispatched quantities & stage
        if is_dc_to_so_upstream:
            from billing.models_sidecar import DeliveryChallanItem
            from django.db.models import Sum, Q, Count
            # 1. Recompute dispatched_quantity for each item in target_doc from all linked non-cancelled DeliveryChallans
            so_items = list(target_doc.items.all())
            challan_items_agg = DeliveryChallanItem.objects.filter(
                challan__sales_order=target_doc,
                challan__status__in=['open', 'billed', 'dispatched', 'delivered']
            ).exclude(challan__status='cancelled').values('source_item_id').annotate(
                total_dispatched=Sum('quantity')
            )
            dispatched_map = {str(row['source_item_id']): (row['total_dispatched'] or 0) for row in challan_items_agg if row['source_item_id']}

            for so_item in so_items:
                calc_qty = dispatched_map.get(str(so_item.id), 0)
                if so_item.dispatched_quantity != calc_qty:
                    so_item.dispatched_quantity = calc_qty
                    so_item.save(update_fields=['dispatched_quantity'])

            # 2. Recalculate SalesOrder stage
            all_fulfilled = len(so_items) > 0 and all(i.is_fulfilled for i in so_items)
            any_dispatched = any((i.dispatched_quantity or 0) > 0 for i in so_items)
            target_doc.stage = 'completed' if all_fulfilled else ('shipped' if any_dispatched else 'new')
            target_doc.save(update_fields=['stage'])

        # Recalculate target total
        cls.recalculate_document_total(target_doc)

        # Continue propagating through target's connections
        cls._propagate_changes(target_doc, updated_items, deleted_items, created_items)

    @classmethod
    def reconcile_sales_order_dispatched_state(cls, order_or_orders, tenant=None):
        """
        Reconciles SalesOrderItem.dispatched_quantity and SalesOrder.stage against
        actual linked non-cancelled DeliveryChallans and converted SalesInvoices.
        Prevents N+1 queries by executing in constant batch queries.
        """
        from billing.models_sidecar import SalesOrder, SalesOrderItem, DeliveryChallanItem
        from billing.models import SalesInvoiceItem
        from django.db.models import Sum

        if not order_or_orders:
            return

        if isinstance(order_or_orders, SalesOrder):
            orders = [order_or_orders]
        elif hasattr(order_or_orders, '__iter__'):
            orders = list(order_or_orders)
        else:
            orders = [order_or_orders]

        if not orders:
            return

        order_ids = [o.id for o in orders]
        tenant_filter = {'challan__created_by': tenant} if tenant else {}

        # 1. Aggregate dispatched quantities from DeliveryChallanItems linked to these orders
        dc_agg = DeliveryChallanItem.objects.filter(
            challan__sales_order_id__in=order_ids,
            challan__status__in=['open', 'billed', 'dispatched', 'delivered'],
            **tenant_filter
        ).exclude(challan__status='cancelled').values('source_item_id').annotate(
            total_dispatched=Sum('quantity')
        )
        dispatched_map = {str(row['source_item_id']): (row['total_dispatched'] or 0) for row in dc_agg if row['source_item_id']}

        # 2. Also check if any order was directly invoiced (without DeliveryChallan)
        order_numbers = [o.order_number for o in orders if getattr(o, 'order_number', None)]
        if order_numbers:
            inv_filter = {'sales_invoice__created_by': tenant} if tenant else {}
            direct_inv_agg = SalesInvoiceItem.objects.filter(
                sales_invoice__po_number__in=order_numbers,
                **inv_filter
            ).values('source_item_id').annotate(
                total_invoiced=Sum('quantity')
            )
            for row in direct_inv_agg:
                s_id = str(row['source_item_id']) if row.get('source_item_id') else None
                if s_id:
                    dispatched_map[s_id] = max(dispatched_map.get(s_id, 0), row['total_invoiced'] or 0)

        # 3. Fetch all items for these orders and check for discrepancies
        items_to_update = []
        so_items_by_order = {}
        all_so_items = SalesOrderItem.objects.filter(order_id__in=order_ids)

        for so_item in all_so_items:
            expected_qty = dispatched_map.get(str(so_item.id), 0)
            if (so_item.dispatched_quantity or 0) != expected_qty:
                so_item.dispatched_quantity = expected_qty
                items_to_update.append(so_item)
            so_items_by_order.setdefault(so_item.order_id, []).append(so_item)

        # Batch update mismatched items in a single query (constant query, no N+1)
        if items_to_update:
            SalesOrderItem.objects.bulk_update(items_to_update, ['dispatched_quantity'])

        # 4. Update stage for each order if needed
        orders_to_update = []
        for order in orders:
            items = so_items_by_order.get(order.id, [])
            all_fulfilled = len(items) > 0 and all(i.is_fulfilled for i in items)
            any_dispatched = any((i.dispatched_quantity or 0) > 0 for i in items)
            expected_stage = 'completed' if all_fulfilled else ('shipped' if any_dispatched else 'new')
            if order.stage != expected_stage and order.stage in ['new', 'shipped', 'completed']:
                order.stage = expected_stage
                orders_to_update.append(order)

        if orders_to_update:
            SalesOrder.objects.bulk_update(orders_to_update, ['stage'])

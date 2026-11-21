"""
Customer Deduplication and Merge Service.

Safely merges duplicate customer records sharing the same GSTIN (or trade identity)
into a canonical primary customer, re-pointing all transactional history
(invoices, challans, orders, quotations, payments, credit notes, ledger entries)
and recomputing the authoritative customer balance.
"""
from django.db import transaction
from django.db.models import Count, Q
from decimal import Decimal

from billing.models import Customer, SalesInvoice, Payment
from billing.models_sidecar import PartyMeta, SalesOrder, DeliveryChallan, Quotation
from billing.models_returns import CreditNote
from billing.balance_sync import recompute_customer_balance


def find_duplicate_customers(tenant):
    """
    Find clusters of duplicate customers for a given tenant.
    Matches primarily by non-empty GSTIN (case-insensitive).
    Returns a list of cluster dicts:
    [
        {
            'gstin': '21AAATJ3251E2ZA',
            'count': 2,
            'customers': [{'id': '...', 'name': '...', 'created_at': '...', 'current_balance': '...'}, ...]
        }
    ]
    """
    # 1. Match by GSTIN
    dup_gstins = (
        Customer.objects.filter(created_by=tenant)
        .exclude(gstin__isnull=True)
        .exclude(gstin__exact='')
        .values('gstin')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
    )

    clusters = []
    seen_ids = set()

    for item in dup_gstins:
        gstin_val = item['gstin'].strip().upper()
        matching_custs = list(
            Customer.objects.filter(created_by=tenant, gstin__iexact=gstin_val)
            .order_by('created_at')
        )
        if len(matching_custs) > 1:
            clusters.append({
                'type': 'gstin',
                'key': gstin_val,
                'count': len(matching_custs),
                'customers': [
                    {
                        'id': str(c.id),
                        'name': c.name,
                        'gstin': c.gstin,
                        'email': c.email or '',
                        'phone': c.phone or '',
                        'current_balance': str(c.current_balance),
                        'created_at': c.created_at.isoformat() if c.created_at else None,
                    }
                    for c in matching_custs
                ]
            })
            for c in matching_custs:
                seen_ids.add(c.id)

    # 2. Match by exact Name (excluding those already in GSTIN clusters)
    dup_names = (
        Customer.objects.filter(created_by=tenant)
        .exclude(id__in=seen_ids)
        .exclude(name__isnull=True)
        .exclude(name__exact='')
        .values('name')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
    )

    for item in dup_names:
        name_val = item['name'].strip()
        matching_custs = list(
            Customer.objects.filter(created_by=tenant, name__iexact=name_val)
            .exclude(id__in=seen_ids)
            .order_by('created_at')
        )
        if len(matching_custs) > 1:
            clusters.append({
                'type': 'name',
                'key': name_val,
                'count': len(matching_custs),
                'customers': [
                    {
                        'id': str(c.id),
                        'name': c.name,
                        'gstin': c.gstin or '',
                        'email': c.email or '',
                        'phone': c.phone or '',
                        'current_balance': str(c.current_balance),
                        'created_at': c.created_at.isoformat() if c.created_at else None,
                    }
                    for c in matching_custs
                ]
            })

    return clusters


def merge_customers(tenant, primary_customer_id, duplicate_customer_id, actor=None):
    """
    Merges duplicate_customer into primary_customer within a single database transaction:
    1. Reassigns all foreign keys (Invoices, Challans, Orders, Quotes, Payments, Credit Notes).
    2. Combines contact info and PartyMeta shipping addresses.
    3. Recomputes primary_customer balance.
    4. Deletes duplicate_customer.
    5. Records an audit log entry.
    """
    if str(primary_customer_id) == str(duplicate_customer_id):
        raise ValueError("Cannot merge a customer into itself.")

    primary = Customer.objects.filter(pk=primary_customer_id, created_by=tenant).first()
    if not primary:
        raise ValueError("Primary customer not found or does not belong to this tenant.")

    duplicate = Customer.objects.filter(pk=duplicate_customer_id, created_by=tenant).first()
    if not duplicate:
        raise ValueError("Duplicate customer not found or does not belong to this tenant.")

    with transaction.atomic():
        reassigned_counts = {}

        # 1. Backfill missing contact fields on primary from duplicate
        save_primary = False
        if not primary.phone and duplicate.phone:
            primary.phone = duplicate.phone
            save_primary = True
        if not primary.email and duplicate.email:
            primary.email = duplicate.email
            save_primary = True
        if not primary.address and duplicate.address:
            primary.address = duplicate.address
            save_primary = True
        if not primary.state and duplicate.state:
            primary.state = duplicate.state
            save_primary = True
        if not primary.gstin and duplicate.gstin:
            primary.gstin = duplicate.gstin.strip().upper()
            save_primary = True
        if (not primary.credit_limit or primary.credit_limit == 0) and duplicate.credit_limit:
            primary.credit_limit = duplicate.credit_limit
            save_primary = True

        if save_primary:
            primary.save()

        # 2. Merge PartyMeta
        primary_meta, _ = PartyMeta.objects.get_or_create(customer=primary)
        dup_meta = PartyMeta.objects.filter(customer=duplicate).first()
        if dup_meta:
            meta_dirty = False
            # Combine unique shipping addresses
            p_ships = list(primary_meta.shipping_addresses or [])
            d_ships = list(dup_meta.shipping_addresses or [])
            merged_ships = p_ships.copy()
            norm_existing = {s.strip().lower() for s in p_ships if isinstance(s, str)}
            for addr in d_ships:
                if isinstance(addr, str) and addr.strip().lower() not in norm_existing:
                    merged_ships.append(addr)
                    norm_existing.add(addr.strip().lower())
            if merged_ships != p_ships:
                primary_meta.shipping_addresses = merged_ships
                meta_dirty = True

            # Loyalty points
            if dup_meta.loyalty_points:
                primary_meta.loyalty_points += dup_meta.loyalty_points
                meta_dirty = True

            # WhatsApp
            if not primary_meta.whatsapp_number and dup_meta.whatsapp_number:
                primary_meta.whatsapp_number = dup_meta.whatsapp_number
                meta_dirty = True

            if meta_dirty:
                primary_meta.save()

        # 3. Repoint Sales Invoices
        inv_count = SalesInvoice.objects.filter(customer=duplicate).update(
            customer=primary,
            customer_name=primary.name
        )
        reassigned_counts['sales_invoices'] = inv_count

        # 4. Repoint Delivery Challans
        dc_count = DeliveryChallan.objects.filter(customer=duplicate).update(
            customer=primary,
            customer_name=primary.name
        )
        reassigned_counts['delivery_challans'] = dc_count

        # 5. Repoint Sales Orders
        so_count = SalesOrder.objects.filter(customer=duplicate).update(
            customer=primary,
            customer_name=primary.name
        )
        reassigned_counts['sales_orders'] = so_count

        # 6. Repoint Quotations
        q_count = Quotation.objects.filter(customer=duplicate).update(
            customer=primary,
            customer_name=primary.name
        )
        reassigned_counts['quotations'] = q_count

        # 7. Repoint Payments
        pay_count = Payment.objects.filter(customer=duplicate).update(customer=primary)
        reassigned_counts['payments'] = pay_count

        # 8. Repoint Credit Notes
        cn_count = CreditNote.objects.filter(customer=duplicate).update(customer=primary)
        reassigned_counts['credit_notes'] = cn_count

        # 9. Repoint General Ledger Entries (if ledger app is active)
        try:
            from ledger.models import GeneralLedgerEntry
            gle_count = GeneralLedgerEntry.objects.filter(customer=duplicate).update(customer=primary)
            reassigned_counts['ledger_entries'] = gle_count
        except Exception:
            pass

        # 10. Linked Vendor counter-part
        try:
            from billing.models import Vendor
            if not getattr(primary, 'linked_vendor', None):
                linked_v = Vendor.objects.filter(linked_customer=duplicate).first()
                if linked_v:
                    linked_v.linked_customer = primary
                    linked_v.save(update_fields=['linked_customer'])
        except Exception:
            pass

        # 11. Delete duplicate customer record (cascading its duplicate PartyMeta)
        dup_name = duplicate.name
        dup_gstin = duplicate.gstin
        dup_id = str(duplicate.id)
        duplicate.delete()

        # 12. Authoritatively recompute primary customer balance
        recompute_customer_balance(primary.id)
        primary.refresh_from_db()

        # 13. Audit Log
        try:
            from audit_log.models import AuditLog
            AuditLog.objects.create(
                tenant=tenant,
                user=actor if actor and getattr(actor, 'is_authenticated', False) else None,
                user_email=getattr(actor, 'email', '') or '',
                action='CUSTOMER_MERGE',
                model_name='Customer',
                object_id=str(primary.id),
                object_repr=f"Merged duplicate customer '{dup_name}' ({dup_id}, GSTIN: {dup_gstin}) into '{primary.name}' ({primary.id})",
            )
        except Exception:
            pass

    return {
        'primary_customer_id': str(primary.id),
        'primary_customer_name': primary.name,
        'deleted_duplicate_id': dup_id,
        'new_balance': str(primary.current_balance),
        'reassigned_counts': reassigned_counts,
    }

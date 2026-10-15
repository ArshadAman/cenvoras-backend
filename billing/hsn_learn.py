"""Tenant-learned HSN → GST rate mappings from invoice/purchase lines."""
from decimal import Decimal, InvalidOperation


def _normalize_hsn(raw):
    if raw is None:
        return ''
    return str(raw).strip().upper()


def _normalize_rate(raw):
    if raw is None or raw == '':
        return None
    try:
        rate = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if rate < 0 or rate > 100:
        return None
    return rate.quantize(Decimal('0.01'))


def learn_hsn_gst_pairs(tenant, pairs):
    """
    Upsert HSN→GST pairs for a tenant.
    pairs: iterable of (hsn_code, gst_rate) or dicts with hsn/tax keys.
    """
    if not tenant or not pairs:
        return 0

    from billing.models_sidecar import TenantLearnedHsnGst

    updated = 0
    for pair in pairs:
        if isinstance(pair, dict):
            hsn = _normalize_hsn(
                pair.get('hsn_sac_code')
                or pair.get('hsn_code')
                or pair.get('hsn')
            )
            rate = _normalize_rate(pair.get('tax') if pair.get('tax') is not None else pair.get('gst_rate'))
        else:
            hsn = _normalize_hsn(pair[0] if len(pair) > 0 else '')
            rate = _normalize_rate(pair[1] if len(pair) > 1 else None)

        if not hsn or rate is None:
            continue

        obj, created = TenantLearnedHsnGst.objects.get_or_create(
            tenant=tenant,
            hsn_code=hsn,
            defaults={'gst_rate': rate, 'usage_count': 1},
        )
        if created:
            updated += 1
            continue

        fields = []
        if obj.gst_rate != rate:
            obj.gst_rate = rate
            fields.append('gst_rate')
        obj.usage_count = (obj.usage_count or 0) + 1
        fields.append('usage_count')
        fields.append('updated_at')
        obj.save(update_fields=fields)
        updated += 1
    return updated


def learn_hsn_gst_from_item_dicts(tenant, items):
    """Learn from serializer item dicts (create/update payloads)."""
    if not items:
        return 0
    pairs = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('row_type') == 'note':
            continue
        hsn = item.get('hsn_sac_code') or item.get('hsn_code')
        product = item.get('product')
        if not hsn and product is not None and hasattr(product, 'hsn_sac_code'):
            hsn = product.hsn_sac_code
        pairs.append({'hsn_sac_code': hsn, 'tax': item.get('tax')})
    return learn_hsn_gst_pairs(tenant, pairs)


def lookup_learned_gst(tenant, hsn_code):
    hsn = _normalize_hsn(hsn_code)
    if not tenant or not hsn:
        return None
    from billing.models_sidecar import TenantLearnedHsnGst

    row = TenantLearnedHsnGst.objects.filter(tenant=tenant, hsn_code=hsn).first()
    if not row:
        # Prefix match: 4-digit learned vs 6/8 digit typed (or reverse)
        row = (
            TenantLearnedHsnGst.objects.filter(tenant=tenant, hsn_code__startswith=hsn[:4])
            .order_by('-usage_count', '-updated_at')
            .first()
            if len(hsn) >= 4
            else None
        )
        if not row and len(hsn) >= 4:
            row = (
                TenantLearnedHsnGst.objects.filter(tenant=tenant, hsn_code__startswith=hsn)
                .order_by('-usage_count', '-updated_at')
                .first()
            )
    if not row:
        return None
    return {
        'hsn_code': row.hsn_code,
        'gst_rate': float(row.gst_rate),
        'usage_count': row.usage_count,
    }

from collections import defaultdict
from decimal import Decimal
from django.utils import timezone
from inventory.models_pricing import Scheme
from inventory.models import Product


def evaluate_schemes_for_items(tenant, items, evaluation_date=None):
    """
    Evaluates active promotional schemes for a list of invoice/order line items.

    Zero N+1 Query Design:
    Performs exactly ONE bulk database query for all product IDs across all line items,
    using select_related('product', 'free_product').

    Args:
        tenant: User instance representing the active tenant.
        items: List of dictionaries representing line item data.
               Each item dict should have:
               - 'product' or 'product_id': UUID, str, or Product instance
               - 'quantity': int or float or Decimal
               - 'price': Decimal, float, or str (optional)
               - 'discount': Decimal, float, or str (optional)
               - 'free_quantity': int (optional, defaults to 0)
        evaluation_date: date object (defaults to today)

    Returns:
        {
            "items": list of evaluated item dicts with updated 'free_quantity', 'discount', and 'applied_scheme',
            "additional_free_items": list of bonus line items for cross-product BOGO offers
        }
    """
    if not items:
        return {"items": [], "additional_free_items": []}

    if evaluation_date is None:
        evaluation_date = timezone.now().date()

    # Extract all distinct product IDs
    product_ids = set()
    for item in items:
        p = item.get('product_id') or item.get('product')
        if hasattr(p, 'id'):
            product_ids.add(p.id)
        elif p:
            product_ids.add(str(p))

    if not product_ids:
        return {"items": items, "additional_free_items": []}

    # Single bulk query for all active schemes matching these products
    active_schemes = Scheme.objects.filter(
        created_by=tenant,
        is_active=True,
        start_date__lte=evaluation_date,
        end_date__gte=evaluation_date,
        product_id__in=product_ids
    ).select_related('product', 'free_product').order_by('-min_qty')

    schemes_by_product = defaultdict(list)
    for scheme in active_schemes:
        schemes_by_product[str(scheme.product_id)].append(scheme)

    evaluated_items = []
    additional_free_items = []

    for item in items:
        item_copy = dict(item)
        p = item_copy.get('product_id') or item_copy.get('product')
        p_id = str(p.id) if hasattr(p, 'id') else (str(p) if p else None)

        try:
            qty = int(item_copy.get('quantity', 0) or 0)
        except (ValueError, TypeError):
            qty = 0

        # Existing free_quantity if manually provided
        existing_free = item_copy.get('free_quantity', 0)
        try:
            existing_free = int(existing_free or 0)
        except (ValueError, TypeError):
            existing_free = 0

        applied_scheme_info = None
        matching_schemes = schemes_by_product.get(p_id, [])

        # Find best applicable scheme for this quantity
        applicable_scheme = None
        for s in matching_schemes:
            if qty >= s.min_qty and s.min_qty > 0:
                applicable_scheme = s
                break

        if applicable_scheme:
            if applicable_scheme.scheme_type == 'bogo':
                multiplier = qty // applicable_scheme.min_qty
                earned_free_qty = multiplier * applicable_scheme.free_qty

                if earned_free_qty > 0:
                    free_prod = applicable_scheme.free_product
                    free_prod_id = str(free_prod.id) if free_prod else None

                    # Same product BOGO
                    if not free_prod_id or free_prod_id == p_id:
                        # Auto-apply free_quantity if not already greater by manual override
                        if existing_free == 0:
                            item_copy['free_quantity'] = earned_free_qty
                        applied_scheme_info = {
                            'id': str(applicable_scheme.id),
                            'name': applicable_scheme.name,
                            'type': 'bogo',
                            'min_qty': applicable_scheme.min_qty,
                            'free_qty': applicable_scheme.free_qty,
                            'earned_free_qty': earned_free_qty,
                            'description': f"Buy {applicable_scheme.min_qty} Get {applicable_scheme.free_qty} Free (+{earned_free_qty} Free)",
                        }
                    else:
                        # Cross-product BOGO (e.g. Buy Phone, Get Case Free)
                        applied_scheme_info = {
                            'id': str(applicable_scheme.id),
                            'name': applicable_scheme.name,
                            'type': 'bogo_cross',
                            'min_qty': applicable_scheme.min_qty,
                            'free_qty': applicable_scheme.free_qty,
                            'earned_free_qty': earned_free_qty,
                            'free_product_id': free_prod_id,
                            'free_product_name': free_prod.name,
                            'description': f"Buy {applicable_scheme.min_qty} Get {applicable_scheme.free_qty} {free_prod.name} Free (+{earned_free_qty} Free)",
                        }
                        # Append a zero-cost line item for the gifted product
                        additional_free_items.append({
                            'product': free_prod,
                            'product_id': free_prod_id,
                            'product_name': free_prod.name,
                            'quantity': 0,
                            'free_quantity': earned_free_qty,
                            'unit': free_prod.unit or 'pcs',
                            'price': Decimal('0.00'),
                            'discount': Decimal('0.00'),
                            'tax': Decimal('0.00'),
                            'amount': Decimal('0.00'),
                            'is_scheme_bonus': True,
                            'applied_scheme': applied_scheme_info,
                        })

            elif applicable_scheme.scheme_type == 'percentage_discount':
                current_discount = Decimal(str(item_copy.get('discount', 0) or 0))
                scheme_discount = Decimal(str(applicable_scheme.discount_percent or 0))
                if current_discount == 0 and scheme_discount > 0:
                    item_copy['discount'] = scheme_discount
                applied_scheme_info = {
                    'id': str(applicable_scheme.id),
                    'name': applicable_scheme.name,
                    'type': 'percentage_discount',
                    'discount_percent': float(applicable_scheme.discount_percent),
                    'description': f"{applicable_scheme.name} ({applicable_scheme.discount_percent}% Off)",
                }

            elif applicable_scheme.scheme_type == 'flat_discount':
                scheme_discount_amount = Decimal(str(applicable_scheme.discount_amount or 0))
                applied_scheme_info = {
                    'id': str(applicable_scheme.id),
                    'name': applicable_scheme.name,
                    'type': 'flat_discount',
                    'discount_amount': float(scheme_discount_amount),
                    'description': f"{applicable_scheme.name} (Flat ₹{scheme_discount_amount} Off)",
                }

        item_copy['applied_scheme'] = applied_scheme_info
        evaluated_items.append(item_copy)

    return {
        'items': evaluated_items,
        'additional_free_items': additional_free_items,
    }

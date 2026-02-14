from decimal import Decimal
from django.utils import timezone
from inventory.models import Product
from inventory.models_pricing import PriceList, PriceListItem, Scheme

def calculate_price(product, customer=None, quantity=1):
    """
    Calculate the effective price for a product given a customer and quantity.
    Returns a dict with price breakdown.
    """
    # 1. Base Price
    base_price = product.sale_price if product.sale_price else product.price
    final_price = base_price
    applied_rule = None
    
    # 2. Price List Override (Priority 1)
    if customer and hasattr(customer, 'meta'):
        category = customer.meta.party_category
        
        # Find active price list for this category
        # We pick the specific item price if available
        # Order by min_qty desc to find the highest tier met
        price_list_item = PriceListItem.objects.filter(
            price_list__party_category=category,
            price_list__is_active=True,
            product=product,
            min_qty__lte=quantity
        ).order_by('-min_qty').first()
        
        if price_list_item:
            final_price = price_list_item.price
            applied_rule = f"Price List: {price_list_item.price_list.name}"
            
    # 3. Scheme Application (Priority 2 - Discounts on top of Price List? Or Instead?)
    # Usually Schemes apply to the *Final Price* OR replace it.
    # Let's assume Schemes give a DISCOUNT on the calculated price.
    
    today = timezone.now().date()
    active_scheme = Scheme.objects.filter(
        product=product,
        is_active=True,
        start_date__lte=today,
        end_date__gte=today,
        min_qty__lte=quantity
    ).first() # Simple: pick first matching scheme
    
    scheme_details = {}
    if active_scheme:
        if active_scheme.scheme_type == 'flat_discount':
            discount = active_scheme.discount_amount
            final_price -= discount
            scheme_details = {'name': active_scheme.name, 'type': 'flat', 'amount': discount}
            applied_rule = f"{applied_rule} + Scheme: {active_scheme.name}" if applied_rule else f"Scheme: {active_scheme.name}"
            
        elif active_scheme.scheme_type == 'percentage_discount':
            discount = (final_price * active_scheme.discount_percent) / 100
            final_price -= discount
            scheme_details = {'name': active_scheme.name, 'type': 'percent', 'amount': discount}
            applied_rule = f"{applied_rule} + Scheme: {active_scheme.name}" if applied_rule else f"Scheme: {active_scheme.name}"
            
        elif active_scheme.scheme_type == 'bogo':
            # BOGO doesn't change unit price usually, but adds free items.
            # But here we return "Effective Price" or just details?
            # Let's just return details for the View to handle free qty.
            free_qty = (quantity // active_scheme.min_qty) * active_scheme.free_qty
            scheme_details = {
                'name': active_scheme.name, 
                'type': 'bogo', 
                'free_product': active_scheme.free_product.id if active_scheme.free_product else None,
                'free_qty': free_qty
            }
            if free_qty > 0:
                applied_rule = f"{applied_rule} + Scheme: {active_scheme.name} (Get {free_qty} Free)" if applied_rule else f"Scheme: {active_scheme.name}"

    return {
        'product_id': product.id,
        'base_price': base_price,
        'final_price': max(final_price, Decimal('0.00')),
        'applied_rule': applied_rule,
        'scheme': scheme_details
    }

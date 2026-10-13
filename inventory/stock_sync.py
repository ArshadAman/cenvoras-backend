"""
Shared helpers to apply stock changes to StockPoint + Product.stock cache.
Used by StockJournal create and StockAdjustmentView.
"""
from django.db import transaction
from django.db.models import F, Sum

from .models import Product, ProductBatch, StockPoint, Warehouse


def reconcile_product_stock(product, save=True):
    """Set Product.stock = sum of StockPoint quantities for that product."""
    return product.recalculate_stock(save=save)


def resolve_batch_for_product(product, batch=None, batch_id=None):
    """
    Resolve a ProductBatch for stock ops.
    If none provided and product has no batches, create/use DEFAULT so non-batch
    products can still be adjusted via StockPoint.
    """
    if batch is not None:
        return batch
    if batch_id:
        return ProductBatch.objects.get(id=batch_id, product=product)

    existing = ProductBatch.objects.filter(product=product).order_by('created_at').first()
    if existing:
        return existing

    return ProductBatch.objects.create(
        product=product,
        batch_number='DEFAULT',
        sale_price=product.sale_price or product.price or 0,
        cost_price=product.price or 0,
        mrp=product.sale_price or product.price or 0,
    )


def apply_stock_delta(product, warehouse, quantity_delta, batch=None, batch_id=None):
    """
    Apply a signed quantity delta to StockPoint for (warehouse, batch),
    then reconcile Product.stock. Returns (stock_point, batch, new_sp_qty).
    """
    batch = resolve_batch_for_product(product, batch=batch, batch_id=batch_id)
    had_prior_batches = ProductBatch.objects.filter(product=product).exclude(pk=batch.pk).exists()
    # If this is effectively the first stockpoint and product had cached stock, seed it
    seed = 0
    if not had_prior_batches and not StockPoint.objects.filter(batch__product=product).exists():
        seed = int(product.stock or 0)

    stock_point, created = StockPoint.objects.select_for_update().get_or_create(
        warehouse=warehouse,
        batch=batch,
        defaults={'quantity': seed},
    )
    if created and seed and quantity_delta != 0:
        # seed already set; apply delta on top
        stock_point.quantity = seed + int(quantity_delta)
    else:
        stock_point.quantity = int(stock_point.quantity or 0) + int(quantity_delta)
    stock_point.save(update_fields=['quantity', 'last_updated'])
    reconcile_product_stock(product, save=True)
    return stock_point, batch, stock_point.quantity

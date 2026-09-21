from django.core.management.base import BaseCommand
from django.db.models import Sum
from inventory.models import Product, ProductBatch, StockPoint


class Command(BaseCommand):
    help = (
        "Reconciles Product.stock cached values with authoritative StockPoint totals. "
        "Operates in O(1) database queries to avoid N+1 performance bottlenecks. "
        "Safely ignores unbatched products."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report discrepancies without updating the database.',
        )
        parser.add_argument(
            '--tenant-id',
            type=str,
            help='Reconcile products for a specific tenant ID.',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Reconcile a single product by UUID.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Batch size for bulk_update.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        tenant_id = options.get('tenant_id')
        product_id = options.get('product_id')
        batch_size = options.get('batch_size') or 500

        product_qs = Product.objects.all()
        if tenant_id:
            product_qs = product_qs.filter(created_by_id=tenant_id)
        if product_id:
            product_qs = product_qs.filter(id=product_id)

        # 1. Identify which products are batched / warehouse-tracked
        batch_qs = ProductBatch.objects.all()
        if product_id:
            batch_qs = batch_qs.filter(product_id=product_id)
        batched_product_ids = set(
            batch_qs.values_list('product_id', flat=True).distinct()
        )

        if not batched_product_ids:
            self.stdout.write(
                self.style.SUCCESS(
                    "No batched products found to reconcile. Unbatched products preserved as-is."
                )
            )
            return

        # 2. In a single aggregation query, compute sum of StockPoints for all batched products
        stock_qs = StockPoint.objects.filter(batch__product_id__in=batched_product_ids)
        stock_aggregates = {
            row['batch__product_id']: (row['total'] or 0)
            for row in stock_qs.values('batch__product_id').annotate(
                total=Sum('quantity')
            )
        }

        # 3. Compare cached Product.stock against calculated authoritative stock
        to_update = []
        discrepancies = []

        for product in product_qs.filter(id__in=batched_product_ids).only('id', 'name', 'stock'):
            actual_stock = stock_aggregates.get(product.id, 0)
            if product.stock != actual_stock:
                discrepancies.append((product, product.stock, actual_stock))
                product.stock = actual_stock
                to_update.append(product)

        total_checked = len(batched_product_ids)
        total_mismatched = len(to_update)

        self.stdout.write(
            f"Checked {total_checked} batched product(s). Found {total_mismatched} discrepancy(ies)."
        )

        for prod, old_stk, new_stk in discrepancies[:20]:
            self.stdout.write(
                f" - {prod.name} (ID: {prod.id}): cached={old_stk} -> actual={new_stk}"
            )
        if len(discrepancies) > 20:
            self.stdout.write(f" ... and {len(discrepancies) - 20} more.")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Would update {total_mismatched} product(s). No changes written."
                )
            )
            return

        # 4. Perform bulk_update in batches
        if to_update:
            Product.objects.bulk_update(to_update, ['stock'], batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully reconciled and updated {total_mismatched} product(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("All batched products are already in sync!")
            )

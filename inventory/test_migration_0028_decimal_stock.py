"""inventory 0028 / billing 0063: integer -> decimal(15,3) keeps every value exactly (incl. negatives)."""
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DecimalStockMigrationTests(TransactionTestCase):
    before = [('inventory', '0027_stock_transfer_challan_wac'), ('billing', '0062_creditnoteitem_quantity_3dp')]
    after = [('inventory', '0028_stock_qty_decimal_3dp'), ('billing', '0063_stock_qty_decimal_3dp')]

    def test_integers_convert_exactly(self):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username='mig_dec', email='mig@x.com', password='x')
        ex = MigrationExecutor(connection)
        ex.migrate(self.before)
        apps = ex.loader.project_state(self.before).apps
        Product = apps.get_model('inventory', 'Product')
        Warehouse = apps.get_model('inventory', 'Warehouse')
        ProductBatch = apps.get_model('inventory', 'ProductBatch')
        StockPoint = apps.get_model('inventory', 'StockPoint')
        p = Product.objects.create(name='P', stock=-7, created_by_id=u.pk, sale_price=1, price=1)
        p2 = Product.objects.create(name='Q', stock=2147483647, created_by_id=u.pk, sale_price=1, price=1)
        w = Warehouse.objects.create(name='W', created_by_id=u.pk)
        b = ProductBatch.objects.create(product=p, batch_number='B')
        sp = StockPoint.objects.create(batch=b, warehouse=w, quantity=35)

        ex = MigrationExecutor(connection)
        ex.loader.build_graph()
        ex.migrate(self.after)
        apps = ex.loader.project_state(self.after).apps
        Product = apps.get_model('inventory', 'Product')
        StockPoint = apps.get_model('inventory', 'StockPoint')
        self.assertEqual(Product.objects.get(pk=p.pk).stock, Decimal('-7'))
        self.assertEqual(Product.objects.get(pk=p2.pk).stock, Decimal('2147483647'))
        self.assertEqual(StockPoint.objects.get(pk=sp.pk).quantity, Decimal('35'))
        self.assertEqual(Product.objects.count(), 2)

        ex = MigrationExecutor(connection)
        ex.loader.build_graph()
        ex.migrate(ex.loader.graph.leaf_nodes())

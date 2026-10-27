"""R22 QA fixes (Lovelish retest): stock cache invalidation, CN messages, CN save query budget."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from billing.tests_qa_r21 import R21CreditNoteDateQtyTests
from inventory.models import Product

LOCMEM = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'r22'}}


@override_settings(CACHES=LOCMEM)
class R22InventoryListStockCacheTests(R21CreditNoteDateQtyTests):
    def setUp(self):
        cache.clear()
        super().setUp()

    def _list_stock(self):
        res = self.client.get("/api/inventory/products/")
        self.assertEqual(res.status_code, 200)
        rows = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        if isinstance(rows, dict):
            rows = rows.get('data') or rows.get('results') or []
        row = next(r for r in rows if str(r['id']) == str(self.product.id))
        return Decimal(str(row['stock']))

    def test_list_shows_stock_restored_by_credit_note(self):
        before = self._list_stock()          # primes the cached list
        with self.captureOnCommitCallbacks(execute=True):
            res = self._post(qty=1, reason="return")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(self._list_stock(), before + 1)

    def test_any_bulk_stock_update_invalidates_list(self):
        before = self._list_stock()
        from django.db.models import F
        with self.captureOnCommitCallbacks(execute=True):
            Product.objects.filter(pk=self.product.pk).update(stock=F('stock') - 2)
        self.assertEqual(self._list_stock(), before - 2)

    def test_non_stock_update_does_not_bump_version(self):
        from inventory.views import get_tenant_catalog_version
        v = get_tenant_catalog_version(self.tenant.id)
        with self.captureOnCommitCallbacks(execute=True):
            Product.objects.filter(pk=self.product.pk).update(description="x")
        self.assertEqual(get_tenant_catalog_version(self.tenant.id), v)


class R22CreditNoteMessageTests(R21CreditNoteDateQtyTests):
    def test_back_dated_message_uses_dd_mm_yyyy(self):
        d = date.today() - timedelta(days=1)
        res = self._post(qty=1, reason="discount", d=d)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn(d.strftime('%d/%m/%Y'), res.data['back_dated']['message'])
        self.assertEqual(res.data['back_dated']['entered_on'], date.today().isoformat())

    def test_before_invoice_message_uses_dd_mm_yyyy(self):
        res = self._post(d=self.inv.invoice_date - timedelta(days=1))
        self.assertIn(self.inv.invoice_date.strftime('%d/%m/%Y'), str(res.data))

    def test_whole_units_message(self):
        self.assertIn('Returns and defective items must be whole units', str(self._post(qty="0.5", reason="defective").data))

    def test_credit_note_save_query_budget(self):
        self.assertEqual(self._post(qty=1, reason="return").status_code, 201)  # first CN seeds the chart of accounts
        with CaptureQueriesContext(connection) as ctx:
            res = self._post(qty=1, reason="return")
        self.assertEqual(res.status_code, 201, res.data)
        import collections, re, os
        if os.environ.get('R22_PROFILE'):
            c = collections.Counter(re.sub(r"'[^']*'|\d+", "?", q['sql'])[:160] for q in ctx.captured_queries)
            for k, v in c.most_common(25):
                print(v, k)
        print(f"\nR22 CN save queries: {len(ctx.captured_queries)}")
        self.assertLessEqual(len(ctx.captured_queries), 60)


class R22CreditNoteListQueryTests(R21CreditNoteDateQtyTests):
    def test_list_query_count_does_not_grow_per_note(self):
        import os
        for _ in range(2):
            self.assertEqual(self._post(qty="0.2", reason="discount").status_code, 201)
        with CaptureQueriesContext(connection) as c2:
            self.client.get("/api/billing/credit-notes/")
        for _ in range(3):
            self.assertEqual(self._post(qty="0.2", reason="discount").status_code, 201)
        with CaptureQueriesContext(connection) as c5:
            self.client.get("/api/billing/credit-notes/")
        if os.environ.get('R22_PROFILE'):
            import collections, re
            c = collections.Counter(re.sub(r"'[^']*'|\d+", "?", q['sql'])[:140] for q in c5.captured_queries)
            for k, v in c.most_common(12):
                print(v, k)
        print(f"\nR22 CN list queries: 2 notes={len(c2.captured_queries)} 5 notes={len(c5.captured_queries)}")
        self.assertEqual(len(c2.captured_queries), len(c5.captured_queries))

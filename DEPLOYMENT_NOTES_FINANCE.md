# Finance and Reporting Rollout Notes

## Scope
This rollout includes:
- Bill payment tracking (`amount_paid`, `payment_status`) on sales and purchase documents.
- Ledger delete restrictions (no debit delete, bill-linked credit delete only when bill is pending).
- Balance Sheet and GST Tax Register drill-down APIs and UI.
- Backup/restore format support for JSON and CSV ZIP.
- Purchase/list validation hardening and batch split validation hardening.

## Deployment Order
1. Deploy backend migrations first.
2. Run `python manage.py migrate`.
3. Deploy backend application code.
4. Deploy frontend application code.

## Backfill and Data Safety
- `payment_status` fields default to `pending` and `amount_paid` defaults to `0`.
- Existing payment create/update/delete flows now refresh invoice payment state.
- If legacy payments exist without invoice linkage, statuses stay based on current persisted values.

## API Compatibility Notes
- Existing JSON backup/export import remains supported.
- New CSV export returns ZIP with module CSV files.
- New CSV import expects ZIP and imports available modules; missing module files are skipped safely.

## Operational Checks After Deploy
1. Create invoice -> record partial payment -> verify `partial_paid` status.
2. Record full payment -> verify `paid` status.
3. Attempt to delete debit ledger entry -> must fail with clear message.
4. Attempt to delete credit entry linked to paid bill -> must fail.
5. Open Balance Sheet and Tax Register drill-down modals -> verify details load.
6. Export backup in JSON and CSV, then import both formats in a non-production tenant.

## Rollback Strategy
- If frontend fails after backend succeeds, keep backend deployed; drill-down features are guarded by request-level error handling in UI.
- If backend issues occur, revert backend release and restore DB from latest backup before reattempting migration.

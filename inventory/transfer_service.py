"""
UAT P1 #15 (Rudra CA): stock transfer between locations.

Same GSTIN (one registration, two godowns):
- Delivery challan only (Rule 55), no GST, never in GSTR-1 (GSTR-1 is built from
  invoices/notes only; a transfer creates neither).
- Valued at weighted-average cost; journal Dr Inventory @B / Cr Inventory @A
  (single Inventory account today, so the pair nets to zero but keeps the audit trail).
- Quantity can't exceed the stock on hand at the source location.
- Above ₹50,000 a warning is shown: Cenvora does not support e-way bills.

Different GSTINs (distinct persons, Sch. I para 2): needs a tax invoice + purchase
bill at the Rule 28 value with stock in transit/short receipt — NOT implemented
here; such transfers are rejected with a clear message.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import F, Sum

CENT = Decimal('0.01')
EWAY_WARNING_THRESHOLD = Decimal('50000.00')
EWAY_WARNING = (
    "This transfer is worth more than ₹50,000. An e-way bill may be required to move these goods. "
    "Cenvora does not support e-way bills — generate one on the e-way bill portal before dispatch."
)
INTER_GSTIN_NOT_SUPPORTED = (
    "These locations are on different GSTINs. A transfer between GSTINs is a taxable supply and "
    "needs a tax invoice plus a purchase bill at the Rule 28 value; this isn't supported yet."
)


class TransferError(ValueError):
    def __init__(self, errors):
        super().__init__(str(errors))
        self.errors = errors


def location_gstin(warehouse, tenant):
    g = (getattr(warehouse, 'gstin', None) or '').strip().upper()
    return g or (getattr(tenant, 'gstin', None) or '').strip().upper()


def product_wac(product, tenant=None):
    """Weighted-average cost per unit (same hierarchy as the stock valuation report)."""
    from inventory.models import StockPoint
    from billing.models import PurchaseBillItem
    sp = StockPoint.objects.filter(batch__product=product, quantity__gt=0, batch__cost_price__gt=0)
    agg = sp.aggregate(q=Sum('quantity'), v=Sum(F('quantity') * F('batch__cost_price')))
    if agg['q'] and agg['v'] is not None:
        return (Decimal(str(agg['v'])) / Decimal(str(agg['q']))).quantize(CENT, rounding=ROUND_HALF_UP)
    rows = PurchaseBillItem.objects.filter(product=product, quantity__gt=0, price__gt=0)
    q = v = Decimal('0')
    for r in rows:
        qty = Decimal(str(r.quantity)) + Decimal(str(getattr(r, 'free_quantity', 0) or 0))
        disc = Decimal(str(getattr(r, 'discount', 0) or 0))
        q += qty
        v += Decimal(str(r.quantity)) * Decimal(str(r.price)) * (Decimal('100') - disc) / Decimal('100')
    if q > 0:
        return (v / q).quantize(CENT, rounding=ROUND_HALF_UP)
    return Decimal(str(product.price or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_transfer(tenant, source, destination, lines):
    """lines: [(product, batch, qty)]. Raises TransferError. Returns (total_value, valued_lines)."""
    from inventory.models import StockPoint
    errors = []
    if source is None or destination is None or source.pk == destination.pk:
        errors.append('Choose two different locations.')
    elif source.created_by_id != tenant.pk or destination.created_by_id != tenant.pk:
        errors.append('Unknown location.')
    elif location_gstin(source, tenant) != location_gstin(destination, tenant):
        errors.append(INTER_GSTIN_NOT_SUPPORTED)
    need = defaultdict(int)
    for product, batch, qty in lines:
        if int(qty) <= 0:
            errors.append(f'{product.name}: quantity must be more than 0.')
        need[(product, batch)] += int(qty)
    if errors:
        raise TransferError(errors)
    valued = []
    total = Decimal('0.00')
    for (product, batch), qty in need.items():
        on_hand = StockPoint.objects.filter(warehouse=source, batch=batch).values_list('quantity', flat=True).first() or 0
        if qty > on_hand:
            errors.append(f'{product.name} ({batch.batch_number}): only {on_hand} in stock at {source.name}; '
                          f"can't transfer {qty}.")
            continue
        rate = product_wac(product, tenant)
        value = (rate * qty).quantize(CENT, rounding=ROUND_HALF_UP)
        valued.append({'product': product, 'batch': batch, 'qty': qty, 'rate': rate, 'value': value})
        total += value
    if errors:
        raise TransferError(errors)
    return total.quantize(CENT), valued


def transfer_warnings(total_value):
    return [EWAY_WARNING] if Decimal(str(total_value)) > EWAY_WARNING_THRESHOLD else []


def next_challan_number(tenant):
    from inventory.models import StockTransfer
    n = StockTransfer.objects.filter(created_by=tenant).exclude(challan_number='').count() + 1
    while StockTransfer.objects.filter(created_by=tenant, challan_number=f'STC-{n:04d}').exists():
        n += 1
    return f'STC-{n:04d}'


def post_transfer_journal(transfer, tenant, entered_by=None):
    """Dr Inventory @destination / Cr Inventory @source at WAC. No GST lines."""
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService
    value = Decimal(str(transfer.total_value or 0)).quantize(CENT)
    if value <= 0:
        return []
    inv = AccountingService.get_or_create_default_accounts(tenant)['1300']
    ref = transfer.challan_number
    common = dict(date=transfer.transfer_date, account=inv, reference=ref, created_by=tenant,
                  entered_by=entered_by)
    return [
        GeneralLedgerEntry.objects.create(
            debit=value, credit=0,
            description=f'Stock transfer {ref}: in at {transfer.destination_warehouse.name} (WAC, no GST)',
            **common),
        GeneralLedgerEntry.objects.create(
            debit=0, credit=value,
            description=f'Stock transfer {ref}: out of {transfer.source_warehouse.name} (WAC, no GST)',
            **common),
    ]

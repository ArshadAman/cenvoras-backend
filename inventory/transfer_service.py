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
    "This transfer is worth more than ₹50,000 and may require an e-way bill (thresholds vary by state); "
    "Cenvora does not generate e-way bills."
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


def fy_label(d):
    """'26-27' for any date in FY 2026-27 (Apr–Mar)."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def next_challan_number(tenant, on_date=None):
    """Rule 55(2): consecutive serial, max 16 chars (A-Z 0-9 - /), unique per FY: STC/26-27/0001."""
    import datetime
    from inventory.models import StockTransfer
    prefix = f"STC/{fy_label(on_date or datetime.date.today())}/"
    used = StockTransfer.objects.filter(created_by=tenant, challan_number__startswith=prefix) \
        .values_list('challan_number', flat=True)
    n = 1 + max((int(x[len(prefix):]) for x in used if x[len(prefix):].isdigit()), default=0)
    num = f"{prefix}{n:04d}"
    assert len(num) <= 16
    return num


def challan_context(transfer):
    """Data for the Rule 55(2) delivery challan print."""
    tenant = transfer.created_by
    name = getattr(tenant, 'business_name', '') or getattr(tenant, 'username', '')
    company_addr = getattr(tenant, 'business_address', '') or ''

    def party(wh):
        return {'name': f"{name} — {wh.name}", 'address': wh.address or company_addr,
                'gstin': location_gstin(wh, tenant)}
    lines = []
    for i, it in enumerate(transfer.items.select_related('product', 'batch').all(), 1):
        lines.append({'sn': i, 'description': it.product.name, 'batch': it.batch.batch_number,
                      'hsn': it.product.hsn_sac_code or '', 'qty': it.quantity, 'unit': it.product.unit or '',
                      'rate': it.rate, 'value': it.value})
    return {'challan_number': transfer.challan_number, 'date': transfer.transfer_date,
            'consignor': party(transfer.source_warehouse), 'consignee': party(transfer.destination_warehouse),
            'lines': lines, 'total_value': transfer.total_value, 'company': name,
            'purpose': 'Transfer of stock between locations of the same registration (not a supply; no GST)',
            'warnings': transfer_warnings(transfer.total_value or 0)}


def render_challan_html(transfer):
    from django.utils.html import escape as e
    c = challan_context(transfer)
    rows = ''.join(
        f"<tr><td>{l['sn']}</td><td>{e(l['description'])}<br><small>Batch {e(l['batch'])}</small></td>"
        f"<td>{e(l['hsn'])}</td><td class=r>{l['qty']} {e(l['unit'])}</td><td class=r>{l['rate']:,.2f}</td>"
        f"<td class=r>{l['value']:,.2f}</td></tr>" for l in c['lines'])

    def p(x, title):
        return (f"<div class=box><b>{title}</b><br>{e(x['name'])}<br>{e(x['address'])}<br>"
                f"GSTIN: {e(x['gstin'] or '—')}</div>")
    warn = ''.join(f"<p class=w>{e(w)}</p>" for w in c['warnings'])
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Delivery Challan {e(c['challan_number'])}</title>
<style>body{{font-family:Arial,sans-serif;font-size:12px;margin:24px}}h1{{font-size:18px;text-align:center}}
table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #444;padding:4px}}.r{{text-align:right}}
.box{{display:inline-block;width:48%;vertical-align:top;border:1px solid #444;padding:6px;margin-bottom:8px}}
.w{{color:#a00}}.sig{{margin-top:48px;text-align:right}}</style></head><body>
<h1>DELIVERY CHALLAN</h1><p style="text-align:center">(Rule 55, CGST Rules 2017) — {e(c['purpose'])}</p>
<p><b>Challan No.:</b> {e(c['challan_number'])} &nbsp; <b>Date:</b> {c['date']:%d-%m-%Y}</p>
{p(c['consignor'], 'Consignor')} {p(c['consignee'], 'Consignee')}
<table><tr><th>#</th><th>Description</th><th>HSN</th><th>Quantity</th><th>Rate (WAC)</th><th>Value (₹)</th></tr>
{rows}<tr><td colspan=5 class=r><b>Total</b></td><td class=r><b>{c['total_value']:,.2f}</b></td></tr></table>
{warn}<div class=sig>For {e(c['company'])}<br><br><br>Authorised Signatory</div></body></html>"""


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

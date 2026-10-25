"""
Shared GST classification and remaining-return helpers used by journals,
GSTR-1/3B, and credit/debit note validation.
"""
from datetime import date
from decimal import Decimal

from billing.gst_utils import (
    calculate_item_tax_breakup,
    get_pos_state_code,
    get_purchase_supplier_state_code,
    get_seller_state_code,
)

ZERO = Decimal('0.00')

EXPORT_TREATMENTS = {
    'export', 'exports', 'sez', 'sezu', 'sezd', 'overseas', 'zero_rated',
    'zero-rated', 'lut', 'bond',
}
NIL_TREATMENTS = {'nil', 'nil_rated', 'nil-rated', 'exempt', 'exempted', 'composition'}
NON_GST_TREATMENTS = {'non_gst', 'nongst', 'non-gst', 'petrol', 'alcohol', 'liquor', 'petroleum'}
INTERSTATE_TREATMENTS = {'interstate', 'igst', 'overseas', 'sez', 'export', 'exports'}
NON_GST_HSN_PREFIXES = ('2710', '2203', '2204', '2205', '2206', '2207', '2208')


def _treatment(obj):
    return (getattr(obj, 'gst_treatment', None) or '').strip().lower()


def classify_outward_supply(invoice, items=None):
    """
    Return one of: taxable, zero_rated, nil_exempt, non_gst.
    Used to split GSTR-3B 3.1(a)/(b)/(c)/(e).
    """
    treatment = _treatment(invoice)
    if treatment in NON_GST_TREATMENTS:
        return 'non_gst'
    if treatment in EXPORT_TREATMENTS:
        return 'zero_rated'
    if treatment in NIL_TREATMENTS:
        return 'nil_exempt'

    if items is None:
        items = [i for i in invoice.items.all() if getattr(i, 'row_type', 'item') != 'note']

    hsn_codes = []
    any_tax = False
    for item in items:
        hsn = (
            getattr(item, 'hsn_sac_code', None)
            or (getattr(getattr(item, 'product', None), 'hsn_sac_code', None) or '')
        )
        hsn_codes.append(str(hsn or '').strip())
        tax = Decimal(str(getattr(item, 'tax', 0) or 0))
        if tax > 0:
            any_tax = True

    if hsn_codes and all(
        any(h.startswith(p) for p in NON_GST_HSN_PREFIXES) for h in hsn_codes if h
    ) and hsn_codes:
        if not any_tax:
            return 'non_gst'

    if not any_tax:
        return 'nil_exempt'
    return 'taxable'


def indian_fy_end(d):
    year = d.year if d.month >= 4 else d.year - 1
    return date(year + 1, 3, 31)


def section34_deadline(invoice_date):
    """Tax credit notes for a FY may be issued until 30 Nov after that FY ends."""
    fy_end = indian_fy_end(invoice_date)
    return date(fy_end.year, 11, 30)


def section34_warning(invoice_date, note_date, has_tax):
    if not invoice_date or not note_date or not has_tax:
        return None
    deadline = section34_deadline(invoice_date)
    if note_date > deadline:
        fy_end = indian_fy_end(invoice_date)
        return (
            f"GST Act s.34: a tax credit note for the original invoice's financial year "
            f"(ending {fy_end.isoformat()}) can only be issued until {deadline.isoformat()}."
        )
    return None


def get_purchase_place_of_supply_code(bill, buyer_state_code):
    pos = getattr(bill, 'place_of_supply', None)
    warehouse = getattr(bill, 'warehouse', None)
    warehouse_state = getattr(warehouse, 'state', None) if warehouse is not None else None
    return get_pos_state_code(pos or warehouse_state, fallback_state=buyer_state_code)


def is_purchase_interstate(bill, buyer_state_code=None):
    """
    Vendor GSTIN/state vs place of supply. gst_treatment interstate/igst wins.
    Never fall back to a 3-way split.
    """
    if buyer_state_code is None:
        user = getattr(bill, 'created_by', None)
        tenant = getattr(user, 'active_tenant', user) if user else None
        buyer_state_code = get_seller_state_code(tenant)

    treatment = _treatment(bill)
    if treatment in INTERSTATE_TREATMENTS:
        return True
    if treatment in ('intrastate', 'intra', 'cgst_sgst', 'local'):
        return False

    supplier = get_purchase_supplier_state_code(bill, buyer_state_code)
    pos = get_purchase_place_of_supply_code(bill, buyer_state_code)
    if not supplier or not pos:
        return False
    return str(supplier).strip() != str(pos).strip()


def _line_taxable(item):
    qty = Decimal(str(getattr(item, 'quantity', 0) or 0))
    price = Decimal(str(getattr(item, 'price', 0) or 0))
    discount = Decimal(str(getattr(item, 'discount', 0) or 0))
    base = qty * price
    disc = ((base * discount) / Decimal('100')).quantize(Decimal('0.01'))
    return (base - disc).quantize(Decimal('0.01'))


def remaining_invoice_returns(invoice, exclude_cn_id=None):
    """Qty per product and taxable remaining on a sales invoice after other credit notes."""
    from billing.models_returns import CreditNoteItem

    sold_qty = {}
    sold_taxable = ZERO
    for item in invoice.items.all():
        if getattr(item, 'row_type', 'item') == 'note':
            continue
        pid = str(item.product_id)
        sold_qty[pid] = sold_qty.get(pid, ZERO) + Decimal(str(item.quantity or 0))
        sold_taxable += _line_taxable(item)

    used_qty = {}
    used_taxable = ZERO
    used_total = ZERO
    cn_items = CreditNoteItem.objects.filter(credit_note__original_invoice=invoice)
    if exclude_cn_id:
        cn_items = cn_items.exclude(credit_note_id=exclude_cn_id)
    for item in cn_items.select_related('credit_note'):
        pid = str(item.product_id)
        used_qty[pid] = used_qty.get(pid, ZERO) + Decimal(str(item.quantity or 0))
        used_taxable += _line_taxable(item)
        used_total += Decimal(str(item.amount or 0))

    remaining_qty = {pid: sold_qty.get(pid, ZERO) - used_qty.get(pid, ZERO) for pid in set(sold_qty) | set(used_qty)}
    return {
        'sold_qty': sold_qty,
        'remaining_qty': remaining_qty,
        'remaining_taxable': sold_taxable - used_taxable,
        'invoice_total': Decimal(str(invoice.total_amount or 0)),
        'used_total': used_total,
    }


def remaining_bill_returns(bill, exclude_dn_id=None):
    """Qty / taxable / ITC remaining on a purchase bill after other debit notes."""
    from billing.models_returns import DebitNoteItem

    buyer = get_seller_state_code(getattr(bill.created_by, 'active_tenant', bill.created_by))
    is_inter = is_purchase_interstate(bill, buyer)

    sold_qty = {}
    sold_taxable = ZERO
    sold_tax = ZERO
    for item in bill.items.all():
        pid = str(item.product_id)
        sold_qty[pid] = sold_qty.get(pid, ZERO) + Decimal(str(item.quantity or 0))
        taxable, tax, *_rest = calculate_item_tax_breakup(item, is_inter)
        sold_taxable += taxable
        sold_tax += tax

    used_qty = {}
    used_taxable = ZERO
    used_tax = ZERO
    dn_items = DebitNoteItem.objects.filter(debit_note__original_bill=bill)
    if exclude_dn_id:
        dn_items = dn_items.exclude(debit_note_id=exclude_dn_id)
    for item in dn_items:
        pid = str(item.product_id)
        used_qty[pid] = used_qty.get(pid, ZERO) + Decimal(str(item.quantity or 0))
        taxable, tax, *_rest = calculate_item_tax_breakup(item, is_inter)
        used_taxable += taxable
        used_tax += tax

    remaining_qty = {pid: sold_qty.get(pid, ZERO) - used_qty.get(pid, ZERO) for pid in set(sold_qty) | set(used_qty)}
    return {
        'remaining_qty': remaining_qty,
        'remaining_taxable': max(sold_taxable - used_taxable, ZERO),
        'remaining_tax': max(sold_tax - used_tax, ZERO),
        'is_interstate': is_inter,
        'sold_taxable': sold_taxable,
        'sold_tax': sold_tax,
    }


def infer_advance_rate(payment):
    """Item GST rate when known from a linked SO / recent invoice; else None (caller uses 18%)."""
    customer = getattr(payment, 'customer', None)
    if not customer:
        return None
    try:
        from billing.models_sidecar import SalesOrder
        so = (
            SalesOrder.objects.filter(created_by=payment.created_by, customer=customer)
            .prefetch_related('items')
            .order_by('-created_at')
            .first()
        )
        if so:
            rates = [
                Decimal(str(i.tax or 0))
                for i in so.items.all()
                if Decimal(str(getattr(i, 'tax', 0) or 0)) > 0
            ]
            if rates:
                return rates[0]
    except Exception:
        pass
    try:
        from billing.models import SalesInvoice
        inv = (
            SalesInvoice.objects.filter(
                created_by=payment.created_by, customer=customer, status='final'
            )
            .prefetch_related('items')
            .order_by('-invoice_date')
            .first()
        )
        if inv:
            rates = [
                Decimal(str(i.tax or 0))
                for i in inv.items.all()
                if getattr(i, 'row_type', 'item') != 'note' and Decimal(str(i.tax or 0)) > 0
            ]
            if rates:
                return rates[0]
    except Exception:
        pass
    return None


def infer_supply_kind(payment):
    kind = (getattr(payment, 'supply_kind', None) or '').strip().lower()
    if kind in ('goods', 'services'):
        return kind
    # SAC (services) typically starts with 99
    customer = getattr(payment, 'customer', None)
    if customer:
        try:
            from billing.models import SalesInvoice
            inv = (
                SalesInvoice.objects.filter(
                    created_by=payment.created_by, customer=customer, status='final'
                )
                .prefetch_related('items')
                .order_by('-invoice_date')
                .first()
            )
            if inv:
                hsns = []
                for i in inv.items.all():
                    if getattr(i, 'row_type', 'item') == 'note':
                        continue
                    h = (i.hsn_sac_code or (i.product.hsn_sac_code if i.product else '') or '').strip()
                    if h:
                        hsns.append(h)
                if hsns and all(h.startswith('99') for h in hsns):
                    return 'services'
                if hsns:
                    return 'goods'
        except Exception:
            pass
    return 'services'


def advance_tax_split(payment, seller_state_code):
    """
    GST on an unlinked customer receipt.
    Goods: no tax (Notfn 66/2017).
    Services: known item rate, else 18% inclusive; known POS, else inter-state fallback.
    """
    kind = infer_supply_kind(payment)
    gross = Decimal(str(payment.amount or 0))
    empty = {
        'kind': kind,
        'rate': ZERO,
        'taxable': gross,
        'tax': ZERO,
        'cgst': ZERO,
        'sgst': ZERO,
        'igst': ZERO,
        'pos': seller_state_code,
        'is_interstate': False,
        'used_fallback_rate': False,
        'used_fallback_pos': False,
    }
    if kind == 'goods' or gross <= 0:
        return empty

    customer = getattr(payment, 'customer', None)
    pos_raw = getattr(payment, 'place_of_supply', None) or (customer.state if customer else None)
    used_fallback_pos = not bool(pos_raw)
    if used_fallback_pos:
        # Law: treat as inter-state when POS cannot be worked out
        pos = '97'
        is_inter = True
    else:
        pos = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
        is_inter = seller_state_code != pos

    rate = infer_advance_rate(payment)
    used_fallback_rate = rate is None
    if rate is None:
        rate = Decimal('18')

    factor = Decimal('1') + (rate / Decimal('100'))
    taxable = (gross / factor).quantize(Decimal('0.01'))
    tax = (gross - taxable).quantize(Decimal('0.01'))
    if is_inter:
        cgst = sgst = ZERO
        igst = tax
    else:
        cgst = (tax / Decimal('2')).quantize(Decimal('0.01'))
        sgst = tax - cgst
        igst = ZERO
    return {
        'kind': kind,
        'rate': rate,
        'taxable': taxable,
        'tax': tax,
        'cgst': cgst,
        'sgst': sgst,
        'igst': igst,
        'pos': pos,
        'is_interstate': is_inter,
        'used_fallback_rate': used_fallback_rate,
        'used_fallback_pos': used_fallback_pos,
    }

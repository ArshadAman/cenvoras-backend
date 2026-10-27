"""
Build GSTR-1 tables once so GSTR-3B 3.1 / 3.2 can reuse the same figures.
"""
from decimal import Decimal
import datetime
import re

from billing.gst_rules import EXPORT_TREATMENTS, section34_deadline, advance_tax_split, classify_outward_supply, customer_pos
from billing.gst_utils import (
    calculate_item_tax_breakup,
    credit_note_tax_breakup,
    get_pos_state_code,
    get_seller_state_code,
    get_uqc,
    validate_hsn_sac_code,
)
from billing.models import Payment, SalesInvoice
from billing.models_returns import CreditNote

ZERO = Decimal('0.00')


# Notification 12/2024-CT: B2CL = inter-state B2C invoice with invoice value MORE THAN Rs 1,00,000.
B2CL_THRESHOLD = Decimal('100000')


def _invoice_pos(inv, seller_state_code):
    # Rudra CA: invoice POS, else customer's GSTIN state, else address state, else seller state.
    pos_raw = inv.place_of_supply or customer_pos(inv.customer)[0] or seller_state_code
    return get_pos_state_code(pos_raw, fallback_state=seller_state_code)


def _is_b2cl(inv, pos_code, seller_state_code, has_gstin):
    return (not has_gstin and seller_state_code != pos_code
            and Decimal(str(inv.total_amount or 0)) > B2CL_THRESHOLD)


B2CS_FIELDS = ('txval', 'camt', 'samt', 'iamt')
NIL_FIELDS = ('nil_amt', 'expt_amt', 'ngsup_amt')


def _nil_key(is_inter, has_gstin):
    return ('INTR' if is_inter else 'INTRA') + ('B2B' if has_gstin else 'B2C')


def _nil_field(inv, bucket):
    if bucket == 'non_gst':
        return 'ngsup_amt'
    treat = (getattr(inv, 'gst_treatment', '') or '').strip().lower()
    return 'expt_amt' if treat in ('exempt', 'exempted') else 'nil_amt'


def _add_nil(nil, sply_ty, field, amt, deadline=None):
    row = nil.setdefault(sply_ty, {'sply_ty': sply_ty, 'nil_amt': 0.0, 'expt_amt': 0.0, 'ngsup_amt': 0.0})
    row[field] = round(row[field] + amt, 2)
    if deadline:
        row['_dl'] = min(row.get('_dl') or deadline, deadline)


def carry_block_reason(tenant, deadline, month_start, today=None):
    """
    Rudra CA s.34(2): a carried CN amount must be DECLARED in a return filed by 30 Nov after the FY.
    Usable only in periods up to October (monthly) or the Jul-Sep quarter (QRMP; is_qrmp() has no
    setting yet so everyone is monthly today), and only if that period's GSTR-1 is filed on or before
    30 Nov (GSTR1Filing.filed_on). Annual-return-filed date not tracked: 30 Nov always used.
    Returns None if usable, else the reason.
    """
    if not deadline:
        return None
    from billing.advances import is_qrmp
    from billing.models import GSTR1Filing
    last = datetime.date(deadline.year, 9 if is_qrmp(tenant) else 10, 1)
    if month_start > last:
        return f"the last period it could be declared in ({last.strftime('%b %Y')}) has passed"
    f = GSTR1Filing.objects.filter(created_by=tenant, period=month_start).first()
    if f and f.filed_on and f.filed_on > deadline:
        return f"the {month_start.strftime('%b %Y')} GSTR-1 was filed on {f.filed_on.isoformat()}, after 30 Nov"
    if not f and (today or datetime.date.today()) > deadline:
        return f"the {month_start.strftime('%b %Y')} GSTR-1 was not filed by {deadline.isoformat()}"
    return None


def floor_cn_rows(b2cs, nil, carry_in, month_start, tenant=None, today=None):
    """
    Rudra CA: a b2cs (POS + rate + supply type) or Table 8 row never goes negative. Excess CN value
    is carried into the next month's same row while s.34(2) allows (carry_block_reason).
    The earliest deadline among netted CNs applies to a row.
    carry_in/out: list of {'table', 'key', 'amts', 'deadline'}. Returns (carry_out, warnings).
    """
    warnings = []
    for c in carry_in:
        why = carry_block_reason(tenant, c['deadline'], month_start, today) if tenant else (
            'deadline passed' if c['deadline'] and month_start > c['deadline'] else None)
        if why:
            warnings.append({'code': 'cn_carry_expired', 'message': (
                f"₹{_fmt(c['amts'])} of credit notes carried forward ({c['table']} {c['key']}) dropped: "
                f"s.34(2) deadline {c['deadline'].isoformat()}; {why}.")})
            continue
        if c['table'] == 'b2cs':
            pos, rt, sply = c['key']
            a = c['amts']
            _add_b2cs(b2cs, pos, sply == 'INTER', rt, a.get('txval', 0), a.get('camt', 0),
                      a.get('samt', 0), a.get('iamt', 0), deadline=c['deadline'])
        else:
            for f, v in c['amts'].items():
                _add_nil(nil, c['key'], f, v, deadline=c['deadline'])
    carry_out = []
    for table, rows, fields, keyf in (
        ('b2cs', b2cs, B2CS_FIELDS, lambda r: (r['pos'], r['rt'], r['sply_ty'])),
        ('table8', list(nil.values()), NIL_FIELDS, lambda r: r['sply_ty']),
    ):
        for r in rows:
            neg = {f: r[f] for f in fields if r[f] < 0}
            dl = r.pop('_dl', None)
            if not neg:
                continue
            for f in neg:
                r[f] = 0.0
            carry_out.append({'table': table, 'key': keyf(r), 'amts': neg, 'deadline': dl})
            warnings.append({'code': 'cn_carried_forward', 'message': (
                f"₹{_fmt(neg)} of credit notes carried forward ({table} {keyf(r)}) to next month.")})
    return carry_out, warnings


def _fmt(amts):
    v = -sum(x for f, x in amts.items() if f in ('txval',) + NIL_FIELDS)
    return f"{v:,.2f}"


def cn_carry_into(tenant, from_date):
    """Excess b2cs / Table 8 CN value carried into the month starting at from_date."""
    from billing.advances import _d, _next_month, month_start
    import datetime as _dt
    start = _d(from_date)
    if start.day != 1:
        return []
    first = (CreditNote.objects.filter(created_by=tenant, original_invoice__isnull=False)
             .order_by('date').values_list('date', flat=True).first())
    if not first or month_start(first) >= start:
        return []
    carry = []
    m = month_start(first)
    while m < start:
        end = _next_month(m) - _dt.timedelta(days=1)
        raw = compute_gstr1(tenant, str(m), str(end), apply_carry=False)
        carry, _w = floor_cn_rows(raw['b2cs'], raw['_nil_map'], carry, m, tenant)
        m = _next_month(m)
    return carry


def _add_b2cs(b2cs, pos_code, is_inter, rt, txval, camt, samt, iamt, deadline=None):
    sply = 'INTER' if is_inter else 'INTRA'
    row = next((b for b in b2cs if b['pos'] == pos_code and b['rt'] == rt and b['sply_ty'] == sply), None)
    if row is None:
        row = {'sply_ty': sply, 'pos': pos_code, 'rt': rt, 'txval': 0.0, 'camt': 0.0,
               'samt': 0.0, 'iamt': 0.0, 'csamt': 0.0}
        b2cs.append(row)
    row['txval'] = round(row['txval'] + txval, 2)
    row['camt'] = round(row['camt'] + camt, 2)
    row['samt'] = round(row['samt'] + samt, 2)
    row['iamt'] = round(row['iamt'] + iamt, 2)
    if deadline:
        row['_dl'] = min(row.get('_dl') or deadline, deadline)


def _customer_gstin(inv):
    if inv.customer and inv.customer.gstin:
        return inv.customer.gstin.strip().upper()
    return (getattr(inv, 'customer_gstin', None) or '').strip().upper()


def compute_gstr1(tenant, from_date, to_date, apply_carry=True):
    seller_state_code = get_seller_state_code(tenant)

    invoices = SalesInvoice.objects.filter(
        created_by=tenant,
        status='final',
        invoice_date__gte=from_date,
        invoice_date__lte=to_date,
    ).select_related('customer').prefetch_related('items__product').order_by('invoice_date', 'invoice_number')

    b2b, b2cs, b2cl = [], [], []
    hsn_dict = {}
    bucket_totals = {
        'taxable': {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO},
        'zero_rated': {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO},
        'nil_exempt': {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO},
        'non_gst': {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO},
    }
    table32 = {}
    nil = {}
    cn_warnings = []

    for inv in invoices:
        items = [i for i in inv.items.all() if getattr(i, 'row_type', 'item') != 'note']
        pos_code = _invoice_pos(inv, seller_state_code)
        is_inter = seller_state_code != pos_code
        cust_gstin = _customer_gstin(inv)
        has_gstin = len(cust_gstin) == 15
        bucket = classify_outward_supply(inv, items)

        items_data = []
        inv_taxable = inv_cgst = inv_sgst = inv_igst = ZERO

        for idx, item in enumerate(items, 1):
            taxable, tax, cgst, sgst, igst, rt = calculate_item_tax_breakup(item, is_inter)
            inv_taxable += taxable
            inv_cgst += cgst
            inv_sgst += sgst
            inv_igst += igst
            items_data.append({
                'num': idx,
                'itm_det': {
                    'txval': float(taxable),
                    'rt': float(rt),
                    'camt': float(cgst),
                    'samt': float(sgst),
                    'iamt': float(igst),
                    'csamt': 0.0,
                }
            })
            raw_hsn = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
            try:
                clean_hsn = validate_hsn_sac_code(raw_hsn, tenant)
            except ValueError:
                continue
            uqc_code = get_uqc(item.unit or (item.product.unit if item.product else 'NOS'))
            desc = item.product.name if item.product else (item.description or 'Item')
            key = (clean_hsn, uqc_code)
            if key not in hsn_dict:
                hsn_dict[key] = {
                    'hsn_sc': clean_hsn, 'desc': desc, 'uqc': uqc_code,
                    'qty': 0, 'val': ZERO, 'txval': ZERO,
                    'camt': ZERO, 'samt': ZERO, 'iamt': ZERO, 'csamt': ZERO,
                }
            hsn_dict[key]['qty'] += item.quantity
            hsn_dict[key]['val'] += item.amount
            hsn_dict[key]['txval'] += taxable
            hsn_dict[key]['camt'] += cgst
            hsn_dict[key]['samt'] += sgst
            hsn_dict[key]['iamt'] += igst

        bucket_totals[bucket]['txval'] += inv_taxable
        bucket_totals[bucket]['iamt'] += inv_igst
        bucket_totals[bucket]['camt'] += inv_cgst
        bucket_totals[bucket]['samt'] += inv_sgst

        if bucket == 'taxable' and is_inter and not has_gstin:
            if pos_code not in table32:
                table32[pos_code] = {'pos': pos_code, 'txval': ZERO, 'iamt': ZERO, 'invoice_ids': set()}
            table32[pos_code]['txval'] += inv_taxable
            table32[pos_code]['iamt'] += inv_igst
            table32[pos_code]['invoice_ids'].add(str(inv.id))

        if bucket in ('nil_exempt', 'non_gst'):
            _add_nil(nil, _nil_key(is_inter, has_gstin), _nil_field(inv, bucket), float(inv_taxable))
        if bucket != 'taxable':
            continue

        if has_gstin:
            inv_entry = {
                'inum': inv.invoice_number,
                'idt': inv.invoice_date.strftime('%d-%m-%Y'),
                'val': float(inv.total_amount),
                'txval': float(inv_taxable),
                'camt': float(inv_cgst),
                'samt': float(inv_sgst),
                'iamt': float(inv_igst),
                'pos': pos_code,
                'rchrg': 'N',
                'inv_typ': 'R',
                'itms': items_data,
            }
            existing = next((b for b in b2b if b['ctin'] == cust_gstin), None)
            if existing:
                existing['inv'].append(inv_entry)
            else:
                b2b.append({'ctin': cust_gstin, 'inv': [inv_entry]})
        elif _is_b2cl(inv, pos_code, seller_state_code, has_gstin):
            b2cl.append({
                'pos': pos_code,
                'inv': [{
                    'inum': inv.invoice_number,
                    'idt': inv.invoice_date.strftime('%d-%m-%Y'),
                    'val': float(inv.total_amount),
                    'txval': float(inv_taxable),
                    'camt': float(inv_cgst),
                    'samt': float(inv_sgst),
                    'iamt': float(inv_igst),
                    'itms': items_data,
                }]
            })
        else:
            for item_payload in items_data:
                det = item_payload['itm_det']
                _add_b2cs(b2cs, pos_code, is_inter, det['rt'], det['txval'], det['camt'],
                          det['samt'], det['iamt'])

    cdnr, cdnur = [], []
    credit_notes = CreditNote.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
        original_invoice__isnull=False,
        original_invoice__status='final',
        gst_time_barred=False,  # commercial CNs (time-barred, no GST) never enter GST returns
    ).select_related('customer', 'original_invoice').prefetch_related('items__product').order_by(
        'date', 'credit_note_number'
    )

    for cn in credit_notes:
        orig_inv = cn.original_invoice
        brk = credit_note_tax_breakup(cn, seller_state_code)
        pos_code = brk['pos_code']
        is_inter = brk['is_interstate']
        cust_gstin = cn.customer.gstin.strip().upper() if (cn.customer and cn.customer.gstin) else ''
        has_gstin = len(cust_gstin) == 15
        cn_taxable = brk['totals']['taxable']
        cn_cgst = brk['totals']['cgst']
        cn_sgst = brk['totals']['sgst']
        cn_igst = brk['totals']['igst']
        cn_val = (cn_taxable + cn_cgst + cn_sgst + cn_igst).quantize(Decimal('0.01'))
        bucket = classify_outward_supply(orig_inv)
        clean_cn_number = re.sub(r'^(CN-)+', 'CN-', cn.credit_note_number)
        deadline = section34_deadline(orig_inv.invoice_date)
        if cn.date > deadline:
            # Rudra CA: a CN dated after the s.34(2) deadline reduces GST nowhere (GSTR-1 tables, Table 8, 3B).
            cn_warnings.append({'code': 'cn_after_s34', 'message': (
                f"{clean_cn_number} excluded from GST returns: dated after the s.34(2) deadline "
                f"{deadline.isoformat()} for invoice {orig_inv.invoice_number}. Re-issue it as a credit note "
                f"without GST.")})
            continue
        cn_tax = cn_cgst + cn_sgst + cn_igst
        if bucket in ('nil_exempt', 'non_gst') and cn_tax > ZERO:
            cn_warnings.append({'code': 'cn_taxed_on_untaxed_invoice', 'message': (
                f"{clean_cn_number} carries GST (₹{cn_tax:,.2f}) but invoice {orig_inv.invoice_number} looks "
                f"nil-rated/exempt/non-GST. Check the data; reported as a taxable credit note.")})
        bucket_totals[bucket]['txval'] -= cn_taxable
        bucket_totals[bucket]['iamt'] -= cn_igst
        bucket_totals[bucket]['camt'] -= cn_cgst
        bucket_totals[bucket]['samt'] -= cn_sgst
        if bucket == 'taxable' and is_inter and not has_gstin and pos_code in table32:
            table32[pos_code]['txval'] -= cn_taxable
            table32[pos_code]['iamt'] -= cn_igst

        for item in cn.items.all():
            raw_hsn = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
            try:
                clean_hsn = validate_hsn_sac_code(raw_hsn, tenant)
            except ValueError:
                continue
            uqc_code = get_uqc(item.unit or (item.product.unit if item.product else 'NOS'))
            taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
            key = (clean_hsn, uqc_code)
            if key in hsn_dict:
                hsn_dict[key]['qty'] -= item.quantity
                hsn_dict[key]['val'] -= item.amount
                hsn_dict[key]['txval'] -= taxable
                hsn_dict[key]['camt'] -= cgst
                hsn_dict[key]['samt'] -= sgst
                hsn_dict[key]['iamt'] -= igst

        cn_items_data = [
            {
                'num': idx,
                'itm_det': {
                    'txval': float(ln['taxable']),
                    'rt': float(ln['rate']),
                    'camt': float(ln['cgst']),
                    'samt': float(ln['sgst']),
                    'iamt': float(ln['igst']),
                    'csamt': 0.0,
                }
            }
            for idx, ln in enumerate(brk['lines'], 1)
        ]
        nt_entry = {
            'nt_num': clean_cn_number,
            'nt_dt': cn.date.strftime('%d-%m-%Y'),
            'inum': orig_inv.invoice_number,
            'idt': orig_inv.invoice_date.strftime('%d-%m-%Y'),
            'val': float(cn_val),
            'txval': float(cn_taxable),
            'camt': float(cn_cgst),
            'samt': float(cn_sgst),
            'iamt': float(cn_igst),
            'ntty': 'C',
            'p_gst': 'N',
            'pos': pos_code,
            'rchrg': 'N',
            'itms': cn_items_data,
        }
        if bucket in ('nil_exempt', 'non_gst') and cn_tax == ZERO:
            # Rudra CA: CN on a nil/exempt/non-GST invoice reduces Table 8 (same inter/intra, B2B/B2C bucket).
            orig_gstin = len(_customer_gstin(orig_inv)) == 15
            _add_nil(nil, _nil_key(is_inter, orig_gstin or has_gstin), _nil_field(orig_inv, bucket),
                     -float(cn_taxable), deadline=deadline)
            continue
        if has_gstin:
            existing_cdnr = next((c for c in cdnr if c['ctin'] == cust_gstin), None)
            if existing_cdnr:
                existing_cdnr['nt'].append(nt_entry)
            else:
                cdnr.append({'ctin': cust_gstin, 'nt': [nt_entry]})
        elif bucket == 'zero_rated' and (getattr(orig_inv, 'gst_treatment', '') or '').strip().lower() in EXPORT_TREATMENTS:
            # cdnur only for CNs on B2CL or export invoices (Rudra CA).
            treat = (orig_inv.gst_treatment or '').strip().lower()
            typ = 'EXPWOP' if (treat in ('lut', 'bond') or cn_igst == ZERO) else 'EXPWP'
            cdnur.append({**{k: v for k, v in nt_entry.items() if k != 'rchrg'}, 'typ': typ})
        elif _is_b2cl(orig_inv, _invoice_pos(orig_inv, seller_state_code), seller_state_code,
                      len(_customer_gstin(orig_inv)) == 15):
            cdnur.append({**{k: v for k, v in nt_entry.items() if k != 'rchrg'}, 'typ': 'B2CL'})
        else:
            # CN on a b2cs invoice: net into b2cs by POS + rate + supply type.
            for ln in brk['lines']:
                _add_b2cs(b2cs, pos_code, is_inter, float(ln['rate']), -float(ln['taxable']),
                          -float(ln['cgst']), -float(ln['sgst']), -float(ln['igst']), deadline=deadline)

    # Rudra CA: never a negative b2cs / Table 8 row; carry the excess forward (s.34(2) limited).
    if apply_carry:
        _m0 = datetime.datetime.strptime(str(from_date), '%Y-%m-%d').date()
        cn_carry_out, floor_warn = floor_cn_rows(b2cs, nil, cn_carry_into(tenant, from_date), _m0, tenant)
        cn_warnings.extend(floor_warn)
    else:
        cn_carry_out = None
    nil_map = nil if not apply_carry else None

    from billing.advances import advance_tables
    at, txpd, at_totals, txpd_totals = advance_tables(tenant, from_date, to_date, seller_state_code)

    hsn_data = []
    for idx, (_key, hval) in enumerate(sorted(hsn_dict.items()), 1):
        hsn_data.append({
            'num': idx,
            'hsn_sc': hval['hsn_sc'],
            'desc': hval['desc'],
            'uqc': hval['uqc'],
            'qty': hval['qty'],
            'val': float(hval['val']),
            'txval': float(hval['txval']),
            'camt': float(hval['camt']),
            'samt': float(hval['samt']),
            'iamt': float(hval['iamt']),
            'csamt': float(hval['csamt']),
        })

    first_cn = credit_notes.first()
    last_cn = credit_notes.last()
    first_cn_num = re.sub(r'^(CN-)+', 'CN-', first_cn.credit_note_number) if first_cn else ''
    last_cn_num = re.sub(r'^(CN-)+', 'CN-', last_cn.credit_note_number) if last_cn else ''

    gstin = tenant.gstin if hasattr(tenant, 'gstin') and tenant.gstin else ''
    fp = datetime.datetime.strptime(str(from_date), '%Y-%m-%d').strftime('%m%Y')

    # 3.1(a) base = taxable invoices − CNs + at − txpd (taxable value and tax)
    a31 = {
        'txval': bucket_totals['taxable']['txval'] + at_totals['txval'] - txpd_totals['txval'],
        'iamt': bucket_totals['taxable']['iamt'] + at_totals['iamt'] - txpd_totals['iamt'],
        'camt': bucket_totals['taxable']['camt'] + at_totals['camt'] - txpd_totals['camt'],
        'samt': bucket_totals['taxable']['samt'] + at_totals['samt'] - txpd_totals['samt'],
    }

    # Never a negative 3.1(a) (Rudra CA, VERIFY): floor each head at 0 and carry the unadjusted
    # advance adjustment into the next month(s). GSTR-1 txpd still goes out in full.
    a31_raw = dict(a31)
    carry_in = {k: ZERO for k in a31}
    carry_from = None
    if apply_carry:
        from billing.advances import carry_into
        carry_in, carry_from = carry_into(tenant, from_date)
    if apply_carry:
        a31 = {k: max(a31_raw[k] - carry_in[k], ZERO) for k in a31_raw}
    carry_out = {k: max(carry_in[k] - a31_raw[k], ZERO) for k in a31_raw}

    return {
        'gstin': gstin,
        'fp': fp,
        'seller_state_code': seller_state_code,
        'b2b': b2b,
        'b2cs': b2cs,
        'b2cl': b2cl,
        'nil': [{k: v for k, v in r.items() if k != '_dl'} for r in nil.values()],
        '_nil_map': nil_map,
        'cn_carry_out': cn_carry_out,
        'cn_warnings': cn_warnings,
        'cdnr': cdnr,
        'cdnur': cdnur,
        'hsn_data': hsn_data,
        'at': at,
        'txpd': txpd,
        'doc_issue': {
            'doc_det': [
                {
                    'doc_num': 1,
                    'doc_typ': 'Invoices for outward supply',
                    'from': invoices.first().invoice_number if invoices.exists() else '',
                    'to': invoices.last().invoice_number if invoices.exists() else '',
                    'totnum': invoices.count(),
                    'canc': 0,
                    'net_issue': invoices.count(),
                },
                {
                    'doc_num': 2,
                    'doc_typ': 'Credit Note',
                    'from': first_cn_num,
                    'to': last_cn_num,
                    'totnum': credit_notes.count(),
                    'canc': 0,
                    'net_issue': credit_notes.count(),
                },
            ]
        },
        'bucket_totals': bucket_totals,
        'table_3_1_a': a31,
        'table_3_1_a_raw': a31_raw,
        'carry_forward_in': carry_in,
        'carry_forward_from': carry_from,
        'carry_forward_out': carry_out,
        'table_3_2': table32,
        'invoices': invoices,
        'credit_notes': credit_notes,
    }

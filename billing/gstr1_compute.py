"""
Build GSTR-1 tables once so GSTR-3B 3.1 / 3.2 can reuse the same figures.
"""
from decimal import Decimal
import datetime
import re

from billing.gst_rules import advance_tax_split, classify_outward_supply
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


def _invoice_pos(inv, seller_state_code):
    pos_raw = inv.place_of_supply or (inv.customer.state if inv.customer else None) or seller_state_code
    return get_pos_state_code(pos_raw, fallback_state=seller_state_code)


def _customer_gstin(inv):
    if inv.customer and inv.customer.gstin:
        return inv.customer.gstin.strip().upper()
    return (getattr(inv, 'customer_gstin', None) or '').strip().upper()


def compute_gstr1(tenant, from_date, to_date):
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
        elif is_inter and inv.total_amount > Decimal('250000'):
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
                existing_b2cs = next(
                    (b for b in b2cs
                     if b['pos'] == pos_code and b['rt'] == det['rt']
                     and b['sply_ty'] == ('INTER' if is_inter else 'INTRA')),
                    None
                )
                if existing_b2cs:
                    existing_b2cs['txval'] = round(existing_b2cs['txval'] + det['txval'], 2)
                    existing_b2cs['camt'] = round(existing_b2cs['camt'] + det['camt'], 2)
                    existing_b2cs['samt'] = round(existing_b2cs['samt'] + det['samt'], 2)
                    existing_b2cs['iamt'] = round(existing_b2cs['iamt'] + det['iamt'], 2)
                else:
                    b2cs.append({
                        'sply_ty': 'INTER' if is_inter else 'INTRA',
                        'pos': pos_code,
                        'rt': det['rt'],
                        'txval': det['txval'],
                        'camt': det['camt'],
                        'samt': det['samt'],
                        'iamt': det['iamt'],
                        'csamt': 0.0,
                    })

    cdnr, cdnur = [], []
    credit_notes = CreditNote.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
        original_invoice__isnull=False,
        original_invoice__status='final',
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
        clean_cn_number = re.sub(r'^(CN-)+', 'CN-', cn.credit_note_number)
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
        if has_gstin:
            existing_cdnr = next((c for c in cdnr if c['ctin'] == cust_gstin), None)
            if existing_cdnr:
                existing_cdnr['nt'].append(nt_entry)
            else:
                cdnr.append({'ctin': cust_gstin, 'nt': [nt_entry]})
        else:
            cdnur.append({
                **{k: v for k, v in nt_entry.items() if k != 'rchrg'},
                'typ': 'B2CL' if (is_inter and cn_val > Decimal('250000')) else 'B2CS',
            })

    at, atadj = [], []
    advance_payments = Payment.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
        invoice__isnull=True,
        customer__isnull=False,
        adjusted_on__isnull=True,
    ).select_related('customer')

    at_totals = {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO}
    for p in advance_payments:
        split = advance_tax_split(p, seller_state_code)
        if split['kind'] == 'goods' or Decimal(str(p.amount or 0)) <= 0:
            continue
        at.append({
            'sply_ty': 'INTER' if split['is_interstate'] else 'INTRA',
            'pos': split['pos'],
            'rt': float(split['rate']),
            'ad_amt': float(p.amount),
            'camt': float(split['cgst']),
            'samt': float(split['sgst']),
            'iamt': float(split['igst']),
            'csamt': 0.0,
        })
        at_totals['txval'] += split['taxable']
        at_totals['iamt'] += split['igst']
        at_totals['camt'] += split['cgst']
        at_totals['samt'] += split['sgst']

    adjusted = Payment.objects.filter(
        created_by=tenant,
        adjusted_on__gte=from_date,
        adjusted_on__lte=to_date,
        customer__isnull=False,
    ).select_related('customer')
    atadj_totals = {'txval': ZERO, 'iamt': ZERO, 'camt': ZERO, 'samt': ZERO}
    for p in adjusted:
        split = advance_tax_split(p, seller_state_code)
        if split['kind'] == 'goods' or split['tax'] <= 0:
            continue
        atadj.append({
            'sply_ty': 'INTER' if split['is_interstate'] else 'INTRA',
            'pos': split['pos'],
            'rt': float(split['rate']),
            'ad_amt': float(p.amount),
            'camt': float(split['cgst']),
            'samt': float(split['sgst']),
            'iamt': float(split['igst']),
            'csamt': 0.0,
        })
        atadj_totals['txval'] += split['taxable']
        atadj_totals['iamt'] += split['igst']
        atadj_totals['camt'] += split['cgst']
        atadj_totals['samt'] += split['sgst']

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

    # 3.1(a) base = taxable invoices − CNs + at − atadj (taxable value and tax)
    a31 = {
        'txval': bucket_totals['taxable']['txval'] + at_totals['txval'] - atadj_totals['txval'],
        'iamt': bucket_totals['taxable']['iamt'] + at_totals['iamt'] - atadj_totals['iamt'],
        'camt': bucket_totals['taxable']['camt'] + at_totals['camt'] - atadj_totals['camt'],
        'samt': bucket_totals['taxable']['samt'] + at_totals['samt'] - atadj_totals['samt'],
    }

    return {
        'gstin': gstin,
        'fp': fp,
        'seller_state_code': seller_state_code,
        'b2b': b2b,
        'b2cs': b2cs,
        'b2cl': b2cl,
        'cdnr': cdnr,
        'cdnur': cdnur,
        'hsn_data': hsn_data,
        'at': at,
        'atadj': atadj,
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
        'table_3_2': table32,
        'invoices': invoices,
        'credit_notes': credit_notes,
    }

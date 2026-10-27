"""
GST Compliance Views
- HSN Summary Report
- Sales Tax Register (CGST/SGST/IGST breakup)
- GSTR-1 JSON Export
- E-Invoice IRN Generation (stub)
- E-Way Bill Generation (stub)
"""
from decimal import Decimal
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Sum, F, Q, Case, When, Value, DecimalField, CharField
from django.db.models.functions import Coalesce
from .models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Customer, Vendor, Payment
from .models_returns import CreditNote, CreditNoteItem, DebitNote, DebitNoteItem
from .gst_utils import (
    get_seller_state_code,
    get_pos_state_code,
    is_interstate_supply,
    get_uqc,
    clean_hsn_sac_code,
    get_debit_note_supplier_state_code,
    get_purchase_supplier_state_code,
    calculate_item_tax_breakup,
    credit_note_tax_breakup,
)

import datetime
import hashlib
import json


# ─── HSN Summary Report ───────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hsn_summary_report(request):
    """
    HSN-wise tax summary — required for GSTR-1 filing.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD&type=sales|purchase
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    report_type = request.query_params.get('type', 'sales')
    seller_state = getattr(tenant, 'state', None) or getattr(request.user, 'state', None)

    hsn_dict = {}

    if report_type == 'sales':
        invoices = SalesInvoice.objects.filter(
            created_by=tenant, status='final'
        ).select_related('customer').prefetch_related('items__product')
        if from_date:
            invoices = invoices.filter(invoice_date__gte=from_date)
        if to_date:
            invoices = invoices.filter(invoice_date__lte=to_date)

        seller_state_code = get_seller_state_code(tenant)
        for inv in invoices:
            pos = inv.place_of_supply or (inv.customer.state if inv.customer else None)
            pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
            is_inter = seller_state_code != pos_code

            for item in inv.items.all():
                if getattr(item, 'row_type', 'item') == 'note':
                    continue
                raw = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
                try:
                    from billing.gst_utils import validate_hsn_sac_code
                    code = validate_hsn_sac_code(raw, tenant)
                except ValueError:
                    continue
                taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)

                if code not in hsn_dict:
                    hsn_dict[code] = {
                        'hsn_code': code,
                        'quantity': 0,
                        'taxable_value': Decimal('0'),
                        'cgst': Decimal('0'),
                        'sgst': Decimal('0'),
                        'igst': Decimal('0'),
                        'total_tax': Decimal('0'),
                        'total_value': Decimal('0'),
                    }
                hsn_dict[code]['quantity'] += item.quantity
                hsn_dict[code]['taxable_value'] += taxable
                hsn_dict[code]['cgst'] += cgst
                hsn_dict[code]['sgst'] += sgst
                hsn_dict[code]['igst'] += igst
                hsn_dict[code]['total_tax'] += tax
                hsn_dict[code]['total_value'] += item.amount

        credit_notes = CreditNote.objects.filter(
            created_by=tenant,
            original_invoice__isnull=False,
            original_invoice__status='final',
        ).select_related('original_invoice').prefetch_related('items__product')
        if from_date:
            credit_notes = credit_notes.filter(date__gte=from_date)
        if to_date:
            credit_notes = credit_notes.filter(date__lte=to_date)
        for cn in credit_notes:
            brk = credit_note_tax_breakup(cn, seller_state_code)
            is_inter = brk['is_interstate']
            for item in cn.items.all():
                raw = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
                try:
                    from billing.gst_utils import validate_hsn_sac_code
                    code = validate_hsn_sac_code(raw, tenant)
                except ValueError:
                    continue
                taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
                if code not in hsn_dict:
                    continue
                hsn_dict[code]['quantity'] -= item.quantity
                hsn_dict[code]['taxable_value'] -= taxable
                hsn_dict[code]['cgst'] -= cgst
                hsn_dict[code]['sgst'] -= sgst
                hsn_dict[code]['igst'] -= igst
                hsn_dict[code]['total_tax'] -= tax
                hsn_dict[code]['total_value'] -= item.amount
        # Advances (GSTR-1 at / txpd) so the register ties to 3.1(a) and the Output GST ledgers
        if from_date and to_date:
            from billing.advances import advance_tables
            at_rows, adj_rows, _t1, _t2 = advance_tables(tenant, from_date, to_date, seller_state_code)
            for sign, rows, label in ((1, at_rows, 'advance'), (-1, adj_rows, 'advance_adjustment')):
                for r in rows:
                    tx = Decimal(str(r['ad_amt'])) * sign
                    c, s_, i = (Decimal(str(r[k])) * sign for k in ('camt', 'samt', 'iamt'))
                    results.append({
                        'id': r['payment_id'], 'invoice_number': 'Advance' if sign > 0 else 'Advance adjusted',
                        'date': str(to_date), 'party_name': '', 'gstin': '',
                        'taxable_value': float(tx), 'cgst': float(c), 'sgst': float(s_), 'igst': float(i),
                        'total_tax': float(c + s_ + i), 'total_amount': float(tx + c + s_ + i), 'doc_type': label,
                    })
                    totals['taxable'] += tx
                    totals['cgst'] += c
                    totals['sgst'] += s_
                    totals['igst'] += i
                    totals['total_tax'] += c + s_ + i
                    totals['total'] += tx + c + s_ + i
    else:
        bills = PurchaseBill.objects.filter(
            created_by=tenant
        ).select_related('vendor').prefetch_related('items__product')
        if from_date:
            bills = bills.filter(bill_date__gte=from_date)
        if to_date:
            bills = bills.filter(bill_date__lte=to_date)

        from billing.gst_rules import is_purchase_interstate
        for bill in bills:
            is_inter = is_purchase_interstate(bill, get_seller_state_code(tenant))

            for item in bill.items.all():
                raw = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
                try:
                    from billing.gst_utils import validate_hsn_sac_code
                    code = validate_hsn_sac_code(raw, tenant)
                except ValueError:
                    continue
                taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)

                if code not in hsn_dict:
                    hsn_dict[code] = {
                        'hsn_code': code,
                        'quantity': 0,
                        'taxable_value': Decimal('0'),
                        'cgst': Decimal('0'),
                        'sgst': Decimal('0'),
                        'igst': Decimal('0'),
                        'total_tax': Decimal('0'),
                        'total_value': Decimal('0'),
                    }
                hsn_dict[code]['quantity'] += item.quantity
                hsn_dict[code]['taxable_value'] += taxable
                hsn_dict[code]['cgst'] += cgst
                hsn_dict[code]['sgst'] += sgst
                hsn_dict[code]['igst'] += igst
                hsn_dict[code]['total_tax'] += tax
                hsn_dict[code]['total_value'] += item.amount

    results = []
    grand_taxable = Decimal('0')
    grand_tax = Decimal('0')
    grand_total = Decimal('0')

    for code in sorted(hsn_dict.keys()):
        row = hsn_dict[code]
        results.append({
            'hsn_code': row['hsn_code'],
            'quantity': row['quantity'],
            'taxable_value': float(row['taxable_value']),
            'cgst': float(row['cgst']),
            'sgst': float(row['sgst']),
            'igst': float(row['igst']),
            'total_tax': float(row['total_tax']),
            'total_value': float(row['total_value']),
        })
        grand_taxable += row['taxable_value']
        grand_tax += row['total_tax']
        grand_total += row['total_value']

    return Response({
        'type': report_type,
        'from_date': from_date,
        'to_date': to_date,
        'count': len(results),
        'summary': {
            'total_taxable_value': float(grand_taxable),
            'total_tax': float(grand_tax),
            'total_value': float(grand_total),
        },
        'results': results,
    })


# ─── Tax Register (CGST / SGST / IGST Breakup) ───────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tax_register(request):
    """
    Invoice-wise GST breakup register.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD&type=sales|purchase
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    report_type = request.query_params.get('type', 'sales')
    seller_state_code = get_seller_state_code(tenant)

    results = []
    totals = {
        'taxable': Decimal('0'),
        'cgst': Decimal('0'),
        'sgst': Decimal('0'),
        'igst': Decimal('0'),
        'total_tax': Decimal('0'),
        'total': Decimal('0')
    }

    if report_type == 'sales':
        invoices = SalesInvoice.objects.filter(
            created_by=tenant, status='final'
        ).select_related('customer').prefetch_related('items__product')
        if from_date:
            invoices = invoices.filter(invoice_date__gte=from_date)
        if to_date:
            invoices = invoices.filter(invoice_date__lte=to_date)
        invoices = invoices.order_by('-invoice_date', '-created_at')

        for inv in invoices:
            pos = inv.place_of_supply or (inv.customer.state if inv.customer else None)
            pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
            is_inter = (seller_state_code != pos_code)

            inv_taxable = Decimal('0')
            inv_cgst = Decimal('0')
            inv_sgst = Decimal('0')
            inv_igst = Decimal('0')
            inv_tax = Decimal('0')

            for item in inv.items.all():
                if getattr(item, 'row_type', 'item') == 'note':
                    continue
                taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
                inv_taxable += taxable
                inv_tax += tax
                inv_cgst += cgst
                inv_sgst += sgst
                inv_igst += igst

            gstin = (inv.customer.gstin.strip().upper() if inv.customer and inv.customer.gstin else '')

            row = {
                'id': str(inv.id),
                'invoice_number': inv.invoice_number,
                'date': str(inv.invoice_date),
                'party_name': inv.customer_name or (inv.customer.name if inv.customer else 'Cash Customer'),
                'gstin': gstin,
                'taxable_value': float(inv_taxable),
                'cgst': float(inv_cgst),
                'sgst': float(inv_sgst),
                'igst': float(inv_igst),
                'total_tax': float(inv_tax),
                'total_amount': float(inv.total_amount),
            }
            results.append(row)

            totals['taxable'] += inv_taxable
            totals['cgst'] += inv_cgst
            totals['sgst'] += inv_sgst
            totals['igst'] += inv_igst
            totals['total_tax'] += inv_tax
            totals['total'] += inv.total_amount

        credit_notes = CreditNote.objects.filter(
            created_by=tenant,
            original_invoice__isnull=False,
            original_invoice__status='final',
        ).select_related('customer', 'original_invoice').prefetch_related('items')
        if from_date:
            credit_notes = credit_notes.filter(date__gte=from_date)
        if to_date:
            credit_notes = credit_notes.filter(date__lte=to_date)
        for cn in credit_notes.order_by('-date', '-created_at'):
            brk = credit_note_tax_breakup(cn, seller_state_code)
            gstin = (cn.customer.gstin.strip().upper() if cn.customer and cn.customer.gstin else '')
            row = {
                'id': str(cn.id),
                'invoice_number': cn.credit_note_number,
                'date': str(cn.date),
                'party_name': cn.customer.name if cn.customer else '',
                'gstin': gstin,
                'taxable_value': -float(brk['totals']['taxable']),
                'cgst': -float(brk['totals']['cgst']),
                'sgst': -float(brk['totals']['sgst']),
                'igst': -float(brk['totals']['igst']),
                'total_tax': -float(brk['totals']['tax']),
                'total_amount': -float(brk['totals']['total']),
                'doc_type': 'credit_note',
            }
            results.append(row)
            totals['taxable'] -= brk['totals']['taxable']
            totals['cgst'] -= brk['totals']['cgst']
            totals['sgst'] -= brk['totals']['sgst']
            totals['igst'] -= brk['totals']['igst']
            totals['total_tax'] -= brk['totals']['tax']
            totals['total'] -= brk['totals']['total']
    else:
        bills = PurchaseBill.objects.filter(
            created_by=tenant
        ).select_related('vendor').prefetch_related('items__product')
        if from_date:
            bills = bills.filter(bill_date__gte=from_date)
        if to_date:
            bills = bills.filter(bill_date__lte=to_date)
        bills = bills.order_by('-bill_date', '-created_at')

        from billing.gst_rules import is_purchase_interstate
        for bill in bills:
            is_inter = is_purchase_interstate(bill, seller_state_code)

            bill_taxable = Decimal('0')
            bill_cgst = Decimal('0')
            bill_sgst = Decimal('0')
            bill_igst = Decimal('0')
            bill_tax = Decimal('0')

            for item in bill.items.all():
                taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
                bill_taxable += taxable
                bill_tax += tax
                bill_cgst += cgst
                bill_sgst += sgst
                bill_igst += igst

            row = {
                'id': str(bill.id),
                'invoice_number': bill.bill_number,
                'date': str(bill.bill_date),
                'party_name': bill.vendor_name,
                'gstin': bill.vendor_gstin or '',
                'taxable_value': float(bill_taxable),
                'cgst': float(bill_cgst),
                'sgst': float(bill_sgst),
                'igst': float(bill_igst),
                'total_tax': float(bill_tax),
                'total_amount': float(bill.total_amount),
            }
            results.append(row)

            totals['taxable'] += bill_taxable
            totals['cgst'] += bill_cgst
            totals['sgst'] += bill_sgst
            totals['igst'] += bill_igst
            totals['total_tax'] += bill_tax
            totals['total'] += bill.total_amount

    return Response({
        'type': report_type,
        'from_date': from_date,
        'to_date': to_date,
        'count': len(results),
        'totals': {k: float(v) for k, v in totals.items()},
        'results': results,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tax_register_invoice_detail(request, invoice_id):
    """
    Drill-down for a single invoice/bill in tax register.
    Query Params: ?type=sales|purchase
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    report_type = request.query_params.get('type', 'sales')
    seller_state_code = get_seller_state_code(tenant)

    if report_type == 'sales':
        try:
            invoice = SalesInvoice.objects.prefetch_related('items__product', 'customer').get(
                id=invoice_id, created_by=tenant
            )
        except SalesInvoice.DoesNotExist:
            return Response({'error': 'Sales invoice not found'}, status=404)

        pos = invoice.place_of_supply or (invoice.customer.state if invoice.customer else None)
        pos_code = get_pos_state_code(pos, fallback_state=seller_state_code)
        is_inter_state = (seller_state_code != pos_code)
        invoice_number = invoice.invoice_number
        invoice_date = invoice.invoice_date
        party_name = invoice.customer_name or (invoice.customer.name if invoice.customer else 'Cash Customer')
        gstin = invoice.customer.gstin if invoice.customer and invoice.customer.gstin else ''
        total_amount = invoice.total_amount
        items = [i for i in invoice.items.all() if getattr(i, 'row_type', 'item') != 'note']
    else:
        try:
            invoice = PurchaseBill.objects.prefetch_related('items__product', 'vendor').get(
                id=invoice_id, created_by=tenant
            )
        except PurchaseBill.DoesNotExist:
            return Response({'error': 'Purchase bill not found'}, status=404)

        vendor_state = getattr(invoice.vendor, 'state', None) if invoice.vendor else None
        vendor_pos_code = get_pos_state_code(vendor_state, fallback_state=seller_state_code)
        is_inter_state = (seller_state_code != vendor_pos_code)
        invoice_number = invoice.bill_number
        invoice_date = invoice.bill_date
        party_name = invoice.vendor_name
        gstin = invoice.vendor_gstin or ''
        total_amount = invoice.total_amount
        items = list(invoice.items.all())

    line_items = []
    total_taxable = Decimal('0')
    total_tax = Decimal('0')
    total_cgst = Decimal('0')
    total_sgst = Decimal('0')
    total_igst = Decimal('0')

    for item in items:
        taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter_state)

        line_items.append({
            'id': str(item.id),
            'product_id': str(item.product_id) if item.product_id else '',
            'product_name': item.product.name if item.product else (getattr(item, 'description', '') or 'Item'),
            'hsn_code': item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '',
            'quantity': item.quantity,
            'unit': item.unit or (item.product.unit if item.product else ''),
            'price': float(item.price),
            'discount': float(item.discount),
            'taxable_value': float(taxable),
            'cgst': float(cgst),
            'sgst': float(sgst),
            'igst': float(igst),
            'tax': float(tax),
            'line_total': float(item.amount),
        })

        total_taxable += taxable
        total_tax += tax
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst

    return Response({
        'id': str(invoice.id),
        'type': report_type,
        'invoice_number': invoice_number,
        'date': str(invoice_date),
        'party_name': party_name,
        'gstin': gstin,
        'is_inter_state': is_inter_state,
        'totals': {
            'taxable': float(total_taxable),
            'cgst': float(total_cgst),
            'sgst': float(total_sgst),
            'igst': float(total_igst),
            'tax': float(total_tax),
            'total_amount': float(total_amount),
        },
        'items': line_items,
    })


# ─── GSTR-1 JSON Export ──────────────────────────────────────

def _adv_rows(rows):
    from billing.advances import merge_advance_rows
    return [{**r, 'pos_label': _pos_label(r['pos'])} for r in merge_advance_rows(rows)]


def _adv_total(rows):
    return {k: round(sum(float(r[k]) for r in rows), 2) for k in ('ad_amt', 'iamt', 'camt', 'samt')}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr1_json_export(request):
    """
    Generate GSTR-1 compliant JSON for a given period.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    if not from_date or not to_date:
        return Response({'error': 'Both from and to dates are required'}, status=400)

    from billing.gstr1_compute import compute_gstr1
    from billing.advances import group_for_portal
    data = compute_gstr1(tenant, from_date, to_date)
    payload = {
        'gstin': data['gstin'],
        'fp': data['fp'],
        'b2b': data['b2b'],
        'b2cs': data['b2cs'],
        'b2cl': data['b2cl'],
        'cdnr': data['cdnr'],
        'cdnur': data['cdnur'],
        'hsn': {
            'data': data['hsn_data'],
            'b2b': data['hsn_data'],
            'b2c': [],
        },
        'doc_issue': data['doc_issue'],
    }
    if data['nil']:
        payload['nil'] = {'inv': data['nil']}  # Table 8 (nil / exempt / non-GST), net of CNs, floored at 0
    from django.conf import settings as dj_settings
    has_adv = bool(data['at'] or data['txpd'])
    if getattr(dj_settings, 'GSTR1_ADVANCES_JSON_ENABLED', False):
        # Table 11A 'at' / 11B (key from settings, default 'txpd'). VERIFY against the offline-tool schema.
        payload['at'] = group_for_portal(data['at'])
        payload[getattr(dj_settings, 'GSTR1_ADVANCE_ADJ_JSON_KEY', 'txpd')] = group_for_portal(data['txpd'])
        note = None
    else:
        note = ('Advances: report via the GSTN offline tool AT/ATADJ sheets (download the CSVs below).'
                if has_adv else None)
    # Underscore key = Cenvora metadata; the frontend strips it before downloading the JSON.
    payload['_cenvora'] = {'advances_in_json': getattr(dj_settings, 'GSTR1_ADVANCES_JSON_ENABLED', False),
                           'advances_note': note, 'has_advances': has_adv,
                           # R18 P0-B: on-screen Table 11A/11B (taxable value), shown even when empty.
                           'advances': {'at': _adv_rows(data['at']), 'txpd': _adv_rows(data['txpd']),
                                        'at_total': _adv_total(data['at']),
                                        'txpd_total': _adv_total(data['txpd'])},
                           'warnings': data['cn_warnings']}
    return Response(payload)


from billing.advances import is_qrmp as _is_qrmp, QRMP_WARNING as _QRMP_WARNING  # noqa: E402


def _pos_label(pos):
    from billing.gst_utils import CODE_TO_STATE_NAME
    pos = str(pos).zfill(2)
    return f"{pos}-{CODE_TO_STATE_NAME.get(pos, '')}".rstrip('-')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr1_advances_csv(request):
    """
    GSTR-1 Table 11A / 11B rows laid out like the GSTN offline tool AT / ATADJ sheets.
    ?from=&to=&sheet=at|atadj. VERIFY: column headers follow the offline-tool Excel template as we
    understand it; the official field tables could not be read. Amounts are TAXABLE value (Rudra CA).
    """
    import csv
    from django.http import HttpResponse
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date, to_date = request.query_params.get('from'), request.query_params.get('to')
    sheet = (request.query_params.get('sheet') or 'at').lower()
    if not from_date or not to_date or sheet not in ('at', 'atadj'):
        return Response({'error': 'from, to and sheet=at|atadj are required'}, status=400)
    from billing.gstr1_compute import compute_gstr1
    data = compute_gstr1(tenant, from_date, to_date)
    rows = data['at'] if sheet == 'at' else data['txpd']
    from billing.advances import merge_advance_rows
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="GSTR1_{sheet.upper()}_{from_date}_{to_date}.csv"'
    w = csv.writer(resp)
    amount_col = 'Gross Advance Received' if sheet == 'at' else 'Gross Advance Adjusted'
    w.writerow(['Place Of Supply', 'Applicable % of Tax Rate', 'Rate', amount_col, 'Cess Amount'])
    # One row per POS + rate + supply type + cess rate (supply type is implied by POS vs supplier state).
    for r in merge_advance_rows(rows):
        w.writerow([_pos_label(r['pos']), '', f"{r['rt']:g}", f"{Decimal(str(r['ad_amt'])).quantize(Decimal('0.01'))}",
                    f"{Decimal(str(r['csamt'])).quantize(Decimal('0.01'))}"])
    w.writerow([])
    w.writerow(['# VERIFY: columns per GSTN offline tool AT/ATADJ sheets; amounts are taxable value '
                '(excluding tax), as per Rudra CA. Generated by Cenvora.'])
    return resp


def _advance_carry_notes(g1):
    """3.1(a) is floored at 0; show what advance adjustment is carried in/out (VERIFY with CA)."""
    out = []
    cin, cout = g1.get('carry_forward_in') or {}, g1.get('carry_forward_out') or {}
    tax = lambda d: sum((Decimal(d.get(k, 0)) for k in ('iamt', 'camt', 'samt')), Decimal('0'))
    if any(Decimal(v) > 0 for v in cin.values()):
        out.append({'code': 'advance_adjustment_carried_in',
                    'message': f"₹{Decimal(cin.get('txval', 0)):,.2f} of advance adjustment carried forward from "
                               f"{g1.get('carry_forward_from') or 'an earlier month'} (tax ₹{tax(cin):,.2f}).",
                    'amounts': {k: float(v) for k, v in cin.items()}})
    if any(Decimal(v) > 0 for v in cout.values()):
        out.append({'code': 'advance_adjustment_carried_out',
                    'message': f"3.1(a) floored at 0: ₹{Decimal(cout.get('txval', 0)):,.2f} of advance adjustment "
                               f"(tax ₹{tax(cout):,.2f}) is carried forward to next month.",
                    'amounts': {k: float(v) for k, v in cout.items()}})
    return out


# ─── GSTR-3B Summary Report ──────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr3b_summary(request):
    """
    GSTR-3B from GSTR-1 tables + Input GST ledgers.
    3.1(a) taxable outward only; 3.1(b) zero-rated; 3.1(c) nil/exempt; 3.1(e) non-GST.
    Filing 4C = max(4A-4B, 0). Excess reversal adds tax only to 3.1(a).
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    if not from_date or not to_date:
        return Response({'error': 'Both from and to dates are required'}, status=400)

    from billing.gstr1_compute import compute_gstr1
    from ledger.models import GeneralLedgerEntry

    g1 = compute_gstr1(tenant, from_date, to_date)
    a31 = {k: Decimal(g1['table_3_1_a'][k]) for k in ('txval', 'iamt', 'camt', 'samt')}
    buckets = g1['bucket_totals']
    table32 = g1['table_3_2']

    def _sum_input_gst(code, *, debit=False, credit=False, purchase_bill=False, debit_note=False):
        qs = GeneralLedgerEntry.objects.filter(
            created_by=tenant,
            date__gte=from_date,
            date__lte=to_date,
            account__code=code,
        )
        if purchase_bill:
            qs = qs.filter(purchase_bill__isnull=False)
        if debit_note:
            qs = qs.filter(debit_note__isnull=False)
        if debit:
            return qs.filter(debit__gt=0).aggregate(s=Sum('debit'))['s'] or Decimal('0.00')
        if credit:
            return qs.filter(credit__gt=0).aggregate(s=Sum('credit'))['s'] or Decimal('0.00')
        return Decimal('0.00')

    itc_igst = _sum_input_gst('1303', debit=True, purchase_bill=True)
    itc_cgst = _sum_input_gst('1301', debit=True, purchase_bill=True)
    itc_sgst = _sum_input_gst('1302', debit=True, purchase_bill=True)
    rev_igst = _sum_input_gst('1303', credit=True, debit_note=True)
    rev_cgst = _sum_input_gst('1301', credit=True, debit_note=True)
    rev_sgst = _sum_input_gst('1302', credit=True, debit_note=True)

    excess_igst = max(rev_igst - itc_igst, Decimal('0.00'))
    excess_cgst = max(rev_cgst - itc_cgst, Decimal('0.00'))
    excess_sgst = max(rev_sgst - itc_sgst, Decimal('0.00'))
    net_itc_igst = max(itc_igst - rev_igst, Decimal('0.00'))
    net_itc_cgst = max(itc_cgst - rev_cgst, Decimal('0.00'))
    net_itc_sgst = max(itc_sgst - rev_sgst, Decimal('0.00'))

    # Excess reversal adds TAX only to 3.1(a), not taxable value
    outward_taxable = a31['txval']
    outward_igst = a31['iamt'] + excess_igst
    outward_cgst = a31['camt'] + excess_cgst
    outward_sgst = a31['samt'] + excess_sgst

    liab_igst, liab_cgst, liab_sgst = outward_igst, outward_cgst, outward_sgst
    rem_itc_igst, rem_itc_cgst, rem_itc_sgst = net_itc_igst, net_itc_cgst, net_itc_sgst

    igst_setoff_igst = min(liab_igst, rem_itc_igst)
    liab_igst -= igst_setoff_igst
    rem_itc_igst -= igst_setoff_igst
    igst_setoff_cgst = min(liab_cgst, rem_itc_igst)
    liab_cgst -= igst_setoff_cgst
    rem_itc_igst -= igst_setoff_cgst
    igst_setoff_sgst = min(liab_sgst, rem_itc_igst)
    liab_sgst -= igst_setoff_sgst
    rem_itc_igst -= igst_setoff_sgst
    cgst_setoff_cgst = min(liab_cgst, rem_itc_cgst)
    liab_cgst -= cgst_setoff_cgst
    rem_itc_cgst -= cgst_setoff_cgst
    cgst_setoff_igst = min(liab_igst, rem_itc_cgst)
    liab_igst -= cgst_setoff_igst
    rem_itc_cgst -= cgst_setoff_igst
    sgst_setoff_sgst = min(liab_sgst, rem_itc_sgst)
    liab_sgst -= sgst_setoff_sgst
    rem_itc_sgst -= sgst_setoff_sgst
    sgst_setoff_igst = min(liab_igst, rem_itc_sgst)
    liab_igst -= sgst_setoff_igst
    rem_itc_sgst -= sgst_setoff_igst

    tot_paid_itc_igst = igst_setoff_igst + cgst_setoff_igst + sgst_setoff_igst
    tot_paid_itc_cgst = igst_setoff_cgst + cgst_setoff_cgst
    tot_paid_itc_sgst = igst_setoff_sgst + sgst_setoff_sgst
    cash_payable_igst, cash_payable_cgst, cash_payable_sgst = liab_igst, liab_cgst, liab_sgst
    total_cash_payable = cash_payable_igst + cash_payable_cgst + cash_payable_sgst

    gstin = g1['gstin']
    fp = g1['fp']
    excess_total = excess_igst + excess_cgst + excess_sgst

    return Response({
        'gstin': gstin,
        'legal_name': getattr(tenant, 'business_name', None) or tenant.username,
        'fp': fp,
        'from_date': from_date,
        'to_date': to_date,
        'warnings': (
            [{
                'code': 'itc_reversal_exceeds_4a',
                'message': (
                    f'ITC reversal exceeds ITC claimed by {float(excess_total):.2f}. '
                    f'That extra tax is added to 3.1(a); filing 4C is zero on those heads.'
                ),
                'true_4c': {
                    'iamt': float(itc_igst - rev_igst),
                    'camt': float(itc_cgst - rev_cgst),
                    'samt': float(itc_sgst - rev_sgst),
                },
                'excess_tax_in_3_1_a': {
                    'iamt': float(excess_igst),
                    'camt': float(excess_cgst),
                    'samt': float(excess_sgst),
                },
            }] if excess_total > 0 else []
        ) + _advance_carry_notes(g1),
        'table_3_1': {
            'outward_taxable': {
                'desc': '(a) Outward taxable supplies (other than zero-rated, nil and exempted)',
                'txval': float(outward_taxable),
                'iamt': float(outward_igst),
                'camt': float(outward_cgst),
                'samt': float(outward_sgst),
                'csamt': 0.0,
            },
            'zero_rated': {
                'desc': '(b) Outward taxable supplies (zero rated)',
                'txval': float(buckets['zero_rated']['txval']),
                'iamt': float(buckets['zero_rated']['iamt']),
                'csamt': 0.0,
            },
            'nil_exempt': {
                'desc': '(c) Other outward supplies (Nil rated, exempted)',
                'txval': float(buckets['nil_exempt']['txval']),
            },
            'inward_reverse_charge': {
                'desc': '(d) Inward supplies (liable to reverse charge)',
                'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0,
            },
            'non_gst_outward': {
                'desc': '(e) Non-GST outward supplies',
                'txval': float(buckets['non_gst']['txval']),
            },
        },
        'table_3_1_1': {
            'eco_taxable': 0.0, 'eco_igst': 0.0, 'eco_cgst': 0.0, 'eco_sgst': 0.0, 'eco_csamt': 0.0,
            'supplies_notified_9_5': {
                'desc': '(i) Taxable supplies on which electronic commerce operator pays tax u/s 9(5)',
                'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0,
            },
            'supplies_through_eco': {
                'desc': '(ii) Taxable supplies made by the registered person through electronic commerce operator',
                'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0,
            },
        },
        'table_3_2': [
            {
                'pos': row['pos'],
                'txval': float(row['txval']),
                'iamt': float(row['iamt']),
                'invoice_count': len(row.get('invoice_ids') or []),
                'b2c': True,
            }
            for row in table32.values() if row['txval'] > 0
        ],
        'table_4_itc': {
            'itc_available': {
                'import_goods': {'iamt': 0.0, 'csamt': 0.0},
                'import_services': {'iamt': 0.0, 'csamt': 0.0},
                'inward_reverse_charge': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'inward_isd': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'all_other_itc': {
                    'iamt': float(itc_igst), 'camt': float(itc_cgst), 'samt': float(itc_sgst), 'csamt': 0.0,
                },
            },
            'itc_reversed': {
                'as_per_rules': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'others': {
                    'iamt': float(rev_igst), 'camt': float(rev_cgst), 'samt': float(rev_sgst), 'csamt': 0.0,
                },
            },
            'net_itc_available': {
                'iamt': float(net_itc_igst), 'camt': float(net_itc_cgst), 'samt': float(net_itc_sgst), 'csamt': 0.0,
            },
            'ineligible_itc': {
                'as_per_section_17_5': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'others': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
            },
        },
        'table_5_exempt': {
            'inter_state': 0.0, 'intra_state': 0.0,
            'from_composition_scheme': {'inter_state': 0.0, 'intra_state': 0.0},
            'non_gst_supply': {'inter_state': 0.0, 'intra_state': 0.0},
        },
        'table_5_1_interest': {
            'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0,
            'interest': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
            'late_fee': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
        },
        'table_6_1_payment': {
            'igst': {
                'tax_payable': float(outward_igst),
                'paid_via_itc': float(tot_paid_itc_igst),
                'tax_paid_cash': float(cash_payable_igst),
                'itc_breakup': {
                    'igst': float(igst_setoff_igst),
                    'cgst': float(cgst_setoff_igst),
                    'sgst': float(sgst_setoff_igst),
                }
            },
            'cgst': {
                'tax_payable': float(outward_cgst),
                'paid_via_itc': float(tot_paid_itc_cgst),
                'tax_paid_cash': float(cash_payable_cgst),
                'itc_breakup': {
                    'igst': float(igst_setoff_cgst),
                    'cgst': float(cgst_setoff_cgst),
                }
            },
            'sgst': {
                'tax_payable': float(outward_sgst),
                'paid_via_itc': float(tot_paid_itc_sgst),
                'tax_paid_cash': float(cash_payable_sgst),
                'itc_breakup': {
                    'igst': float(igst_setoff_sgst),
                    'sgst': float(sgst_setoff_sgst),
                }
            },
            'total_tax_payable': float(outward_igst + outward_cgst + outward_sgst),
            'total_net_itc': float(net_itc_igst + net_itc_cgst + net_itc_sgst),
            'total_itc_utilized': float(tot_paid_itc_igst + tot_paid_itc_cgst + tot_paid_itc_sgst),
            'total_tax_paid_cash': float(total_cash_payable),
        }
    })


# ─── E-Invoice IRN Generation (Stub) ─────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_einvoice(request):
    """
    Generate E-Invoice IRN for a sales invoice.
    Body: { "invoice_id": "uuid" }
    
    NOTE: This is a stub. Production requires NIC API credentials.
    It generates the IRN hash locally for development/testing.
    """
    invoice_id = request.data.get('invoice_id')
    if not invoice_id:
        return Response({'error': 'invoice_id is required'}, status=400)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        invoice = SalesInvoice.objects.select_related('customer').prefetch_related('items__product').get(
            pk=invoice_id, created_by=tenant
        )
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Invoice not found'}, status=404)

    gstin = tenant.gstin if hasattr(tenant, 'gstin') and tenant.gstin else 'UNREGISTERED'

    # Build IRN payload (simplified NIC schema)
    irn_payload = {
        'Version': '1.1',
        'TranDtls': {
            'TaxSch': 'GST',
            'SupTyp': 'B2B' if (invoice.customer and invoice.customer.gstin) else 'B2C',
            'RegRev': 'N',
        },
        'DocDtls': {
            'Typ': 'INV',
            'No': invoice.invoice_number,
            'Dt': invoice.invoice_date.strftime('%d/%m/%Y'),
        },
        'SellerDtls': {
            'Gstin': gstin,
            'LglNm': tenant.business_name or tenant.username,
            'Addr1': tenant.business_address[:100] if hasattr(tenant, 'business_address') and tenant.business_address else 'N/A',
            'Loc': tenant.state if hasattr(tenant, 'state') and tenant.state else '',
            'Pin': 0,
            'Stcd': tenant.state if hasattr(tenant, 'state') and tenant.state else '',
        },
        'BuyerDtls': {
            'Gstin': invoice.customer.gstin if invoice.customer and invoice.customer.gstin else 'URP',
            'LglNm': invoice.customer_name or 'Cash Customer',
            'Pos': invoice.place_of_supply or '',
            'Addr1': invoice.customer.address[:100] if invoice.customer and invoice.customer.address else 'N/A',
            'Loc': '',
            'Pin': 0,
            'Stcd': invoice.place_of_supply or '',
        },
        'ItemList': [],
        'ValDtls': {
            'TotInvVal': float(invoice.total_amount),
        }
    }

    for idx, item in enumerate(invoice.items.all(), 1):
        irn_payload['ItemList'].append({
            'SlNo': str(idx),
            'PrdDesc': item.product.name,
            'HsnCd': item.hsn_sac_code or item.product.hsn_sac_code or '',
            'Qty': item.quantity,
            'Unit': item.unit or item.product.unit or 'NOS',
            'UnitPrice': float(item.price),
            'TotAmt': float(item.amount),
            'Discount': float(item.discount),
            'TxblVal': float(item.amount - item.tax),
            'GstRt': float(item.product.tax) if hasattr(item.product, 'tax') else 0,
        })

    # Generate stub IRN hash
    irn_hash = hashlib.sha256(json.dumps(irn_payload, sort_keys=True).encode()).hexdigest()

    return Response({
        'irn': irn_hash,
        'ack_no': f'ACK-{invoice.invoice_number}',
        'ack_date': datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        'signed_invoice': irn_payload,
        'status': 'stub',
        'message': 'IRN generated locally (stub). Connect NIC API for production.',
    })


# ─── E-Way Bill Generation ───────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_eway_bill(request):
    """
    Generate E-Way Bill for a sales invoice.
    Body: {
        "invoice_id": "uuid",
        "transporter_id": "GSTIN of transporter (optional)",
        "vehicle_no": "MH01AB1234",
        "distance_km": 150
    }
    Updates the EWayBill model record.
    """
    invoice_id = request.data.get('invoice_id')
    if not invoice_id:
        return Response({'error': 'invoice_id is required'}, status=400)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        invoice = SalesInvoice.objects.select_related('customer').get(
            pk=invoice_id, created_by=tenant
        )
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Invoice not found'}, status=404)

    if invoice.total_amount < 50000:
        return Response({
            'warning': 'E-Way Bill is only required for goods valued at ₹50,000 or above.',
            'total_amount': float(invoice.total_amount),
        }, status=400)

    vehicle_no = request.data.get('vehicle_no', '')
    transporter_id = request.data.get('transporter_id', '')
    distance_km = request.data.get('distance_km', 0)

    # Generate stub E-Way Bill number
    eway_number = f'EWB{invoice.invoice_number}{datetime.datetime.now().strftime("%Y%m%d%H%M")}'

    # Create or update EWayBill record
    eway_bill, created = EWayBill.objects.update_or_create(
        invoice=invoice,
        defaults={
            'eway_bill_number': eway_number,
            'vehicle_number': vehicle_no,
            'transporter_id': transporter_id,
            'distance_km': distance_km,
            'generated_at': datetime.datetime.now(),
            'valid_until': datetime.datetime.now() + datetime.timedelta(days=1),
            'status': 'generated',
            'created_by': tenant,
        }
    )

    return Response({
        'eway_bill_number': eway_number,
        'invoice_number': invoice.invoice_number,
        'vehicle_no': vehicle_no,
        'distance_km': distance_km,
        'generated_at': eway_bill.generated_at,
        'valid_until': eway_bill.valid_until,
        'status': 'generated',
        'message': 'E-Way Bill generated locally (stub). Connect NIC API for production.',
    }, status=status.HTTP_201_CREATED if created else 200)



@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def gstr1_filing_status(request):
    """
    GSTR-1 filed flag per month (Cenvora does not file returns; the user records that they filed).
    GET  ?period=YYYY-MM            -> {period, filed, filed_on}
    POST {period: 'YYYY-MM', filed: true|false, confirm: true}
    Unfiled by default. Filed periods are not rewritten by advance applications.
    """
    import datetime as _dt
    from billing.models import GSTR1Filing
    tenant = getattr(request.user, 'active_tenant', request.user)
    raw = request.query_params.get('period') if request.method == 'GET' else request.data.get('period')
    try:
        period = _dt.date.fromisoformat(f"{str(raw)[:7]}-01")
    except (ValueError, TypeError):
        return Response({'error': 'period must be YYYY-MM'}, status=400)
    if request.method == 'POST':
        if not request.data.get('confirm'):
            return Response({'error': 'Confirmation required.'}, status=400)
        if request.data.get('filed', True):
            from billing.advances import today_ist
            GSTR1Filing.objects.get_or_create(created_by=tenant, period=period,
                                              defaults={'filed_on': today_ist(), 'marked_by': request.user})
        else:
            GSTR1Filing.objects.filter(created_by=tenant, period=period).delete()
    row = GSTR1Filing.objects.filter(created_by=tenant, period=period).first()
    return Response({'period': period.strftime('%Y-%m'), 'filed': bool(row),
                     'filed_on': str(row.filed_on) if row else None,
                     # No filing-frequency setting exists yet; QRMP is read from a future attribute only.
                     'qrmp': _is_qrmp(tenant), 'qrmp_warning': _QRMP_WARNING if _is_qrmp(tenant) else None})

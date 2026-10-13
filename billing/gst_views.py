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
from .gst_utils import get_seller_state_code, get_pos_state_code, is_interstate_supply, get_uqc, clean_hsn_sac_code

import datetime
import hashlib
import json


def calculate_item_tax_breakup(item, is_interstate=False):
    """
    Standard tax calculation for SalesInvoiceItem, PurchaseBillItem, CreditNoteItem, DebitNoteItem.
    Returns: (taxable_amount, tax_amount, cgst, sgst, igst, tax_rate)
    """
    quantity = Decimal(str(getattr(item, 'quantity', 0) or 0))
    price = Decimal(str(getattr(item, 'price', 0) or 0))
    discount = Decimal(str(getattr(item, 'discount', 0) or 0))
    line_amount = Decimal(str(getattr(item, 'amount', 0) or 0))
    tax_rate = Decimal(str(getattr(item, 'tax', 0) or 0))

    base_amount = quantity * price
    discount_amount = ((base_amount * discount) / Decimal('100')).quantize(Decimal('0.01'))
    calc_taxable = (base_amount - discount_amount).quantize(Decimal('0.01'))

    if quantity > 0 and price > 0:
        taxable_amount = calc_taxable
        if tax_rate > 0:
            tax_amount = ((taxable_amount * tax_rate) / Decimal('100')).quantize(Decimal('0.01'))
        elif line_amount > taxable_amount:
            tax_amount = line_amount - taxable_amount
        else:
            tax_amount = Decimal('0.00')
    else:
        if tax_rate > 0 and line_amount > 0:
            taxable_amount = (line_amount / (Decimal('1') + (tax_rate / Decimal('100')))).quantize(Decimal('0.01'))
            tax_amount = line_amount - taxable_amount
        else:
            taxable_amount = line_amount
            tax_amount = Decimal('0.00')

    if is_interstate:
        igst = tax_amount
        cgst = Decimal('0.00')
        sgst = Decimal('0.00')
    else:
        cgst = (tax_amount / Decimal('2')).quantize(Decimal('0.01'))
        sgst = tax_amount - cgst
        igst = Decimal('0.00')

    return taxable_amount, tax_amount, cgst, sgst, igst, tax_rate


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

        for inv in invoices:
            pos = inv.place_of_supply or (inv.customer.state if inv.customer else None)
            is_inter = bool(seller_state and pos and seller_state.strip().upper() != pos.strip().upper())

            for item in inv.items.all():
                if getattr(item, 'row_type', 'item') == 'note':
                    continue
                code = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or 'N/A').strip()
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
    else:
        bills = PurchaseBill.objects.filter(
            created_by=tenant
        ).select_related('vendor').prefetch_related('items__product')
        if from_date:
            bills = bills.filter(bill_date__gte=from_date)
        if to_date:
            bills = bills.filter(bill_date__lte=to_date)

        for bill in bills:
            vendor_state = getattr(bill.vendor, 'state', None) if bill.vendor else None
            is_inter = bool(seller_state and vendor_state and seller_state.strip().upper() != vendor_state.strip().upper())

            for item in bill.items.all():
                code = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or 'N/A').strip()
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
    else:
        bills = PurchaseBill.objects.filter(
            created_by=tenant
        ).select_related('vendor').prefetch_related('items__product')
        if from_date:
            bills = bills.filter(bill_date__gte=from_date)
        if to_date:
            bills = bills.filter(bill_date__lte=to_date)
        bills = bills.order_by('-bill_date', '-created_at')

        for bill in bills:
            vendor_state = getattr(bill.vendor, 'state', None) if bill.vendor else None
            vendor_pos_code = get_pos_state_code(vendor_state, fallback_state=seller_state_code)
            is_inter = (seller_state_code != vendor_pos_code)

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

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr1_json_export(request):
    """
    Generate GSTR-1 compliant JSON for a given period.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    Output JSON follows NIC portal GSTR-1 schema with:
    - b2b: Sales to registered customers
    - b2cs: Small B2C sales
    - b2cl: Large inter-state B2C sales (> 2.5L)
    - cdnr: Credit Notes issued to registered persons
    - cdnur: Credit Notes issued to unregistered persons
    - hsn: HSN summary
    - doc_issue: Documents issued summary (Section 13)
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')

    if not from_date or not to_date:
        return Response({'error': 'Both from and to dates are required'}, status=400)

    # CA AUDIT (Issue 2): Derive seller state from 2-digit prefix of seller GSTIN first
    seller_state_code = get_seller_state_code(tenant)

    invoices = SalesInvoice.objects.filter(
        created_by=tenant,
        status='final',
        invoice_date__gte=from_date,
        invoice_date__lte=to_date,
    ).select_related('customer').prefetch_related('items__product').order_by('invoice_date', 'invoice_number')

    b2b = []   # 4A, 4B, 4C, 6B, 6C (with GSTIN)
    b2cs = []  # 7 (without GSTIN, intrastate or interstate <= 2.5L)
    b2cl = []  # 5A, 5B (without GSTIN, interstate > 2.5L)

    hsn_dict_b2b = {}
    hsn_dict_b2c = {}

    import re

    for inv in invoices:
        items = [i for i in inv.items.all() if getattr(i, 'row_type', 'item') != 'note']
        pos_raw = inv.place_of_supply or (inv.customer.state if inv.customer else None) or seller_state_code
        # CA AUDIT (Issue 4): pos MUST be the 2-digit state code (e.g. "21")
        pos_code = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
        is_inter = (seller_state_code != pos_code)

        cust_gstin = ''
        if inv.customer and inv.customer.gstin:
            cust_gstin = inv.customer.gstin.strip().upper()
        elif hasattr(inv, 'customer_gstin') and inv.customer_gstin:
            cust_gstin = inv.customer_gstin.strip().upper()

        has_gstin = (len(cust_gstin) == 15)

        items_data = []
        inv_taxable = Decimal('0.00')
        inv_cgst = Decimal('0.00')
        inv_sgst = Decimal('0.00')
        inv_igst = Decimal('0.00')

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

            # CA AUDIT (Issue 5): HSN mandatory, never blank or "N/A", include uqc and qty, split B2B and B2C
            raw_hsn = (item.hsn_sac_code or (item.product.hsn_sac_code if item.product else '') or '').strip()
            clean_hsn = clean_hsn_sac_code(raw_hsn)
            uqc_code = get_uqc(item.unit or (item.product.unit if item.product else 'NOS'))
            desc = item.product.name if item.product else (item.description or 'Item')

            target_hsn_dict = hsn_dict_b2b if has_gstin else hsn_dict_b2c
            hsn_key = (clean_hsn, uqc_code)
            if hsn_key not in target_hsn_dict:
                target_hsn_dict[hsn_key] = {
                    'hsn_sc': clean_hsn,
                    'desc': desc,
                    'uqc': uqc_code,
                    'qty': 0,
                    'val': Decimal('0.00'),
                    'txval': Decimal('0.00'),
                    'camt': Decimal('0.00'),
                    'samt': Decimal('0.00'),
                    'iamt': Decimal('0.00'),
                    'csamt': Decimal('0.00'),
                }
            target_hsn_dict[hsn_key]['qty'] += item.quantity
            target_hsn_dict[hsn_key]['val'] += item.amount
            target_hsn_dict[hsn_key]['txval'] += taxable
            target_hsn_dict[hsn_key]['camt'] += cgst
            target_hsn_dict[hsn_key]['samt'] += sgst
            target_hsn_dict[hsn_key]['iamt'] += igst

        if has_gstin:
            # B2B Invoice
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
                b2b.append({
                    'ctin': cust_gstin,
                    'inv': [inv_entry]
                })
        else:
            # B2C Invoice
            if is_inter and inv.total_amount > Decimal('250000'):
                # B2C Large (> 2.5L Interstate)
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
                # B2C Small
                for item_payload in items_data:
                    det = item_payload['itm_det']
                    # Group by pos + rate
                    existing_b2cs = next(
                        (b for b in b2cs if b['pos'] == pos_code and b['rt'] == det['rt'] and b['sply_ty'] == ('INTER' if is_inter else 'INTRA')),
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

    # Credit Notes (CDNR & CDNUR)
    cdnr = []
    cdnur = []

    credit_notes = CreditNote.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
    ).select_related('customer', 'original_invoice').prefetch_related('items__product').order_by('date', 'credit_note_number')

    for cn in credit_notes:
        cn_items = list(cn.items.all())
        orig_inv = cn.original_invoice
        pos_raw = (cn.customer.state if cn.customer else None) or (orig_inv.place_of_supply if orig_inv else None) or seller_state_code
        pos_code = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
        is_inter = (seller_state_code != pos_code)

        cust_gstin = cn.customer.gstin.strip().upper() if (cn.customer and cn.customer.gstin) else ''
        has_gstin = (len(cust_gstin) == 15)

        cn_items_data = []
        cn_taxable = Decimal('0.00')
        cn_cgst = Decimal('0.00')
        cn_sgst = Decimal('0.00')
        cn_igst = Decimal('0.00')

        for idx, item in enumerate(cn_items, 1):
            taxable, tax, cgst, sgst, igst, rt = calculate_item_tax_breakup(item, is_inter)
            cn_taxable += taxable
            cn_cgst += cgst
            cn_sgst += sgst
            cn_igst += igst

            cn_items_data.append({
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

        orig_inv_num = orig_inv.invoice_number if orig_inv else 'N/A'
        orig_inv_dt = orig_inv.invoice_date.strftime('%d-%m-%Y') if orig_inv else cn.date.strftime('%d-%m-%Y')
        clean_cn_number = re.sub(r'^(CN-)+', 'CN-', cn.credit_note_number)

        # CA AUDIT (Issue 1): Show val, txval, camt, samt, iamt, note type "C", original inum and idt
        if has_gstin:
            nt_entry = {
                'nt_num': clean_cn_number,
                'nt_dt': cn.date.strftime('%d-%m-%Y'),
                'inum': orig_inv_num,
                'idt': orig_inv_dt,
                'val': float(cn.total_amount),
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
            existing_cdnr = next((c for c in cdnr if c['ctin'] == cust_gstin), None)
            if existing_cdnr:
                existing_cdnr['nt'].append(nt_entry)
            else:
                cdnr.append({
                    'ctin': cust_gstin,
                    'nt': [nt_entry]
                })
        else:
            cdnur.append({
                'typ': 'B2CL' if (is_inter and cn.total_amount > Decimal('250000')) else 'B2CS',
                'nt_num': clean_cn_number,
                'nt_dt': cn.date.strftime('%d-%m-%Y'),
                'inum': orig_inv_num,
                'idt': orig_inv_dt,
                'val': float(cn.total_amount),
                'txval': float(cn_taxable),
                'camt': float(cn_cgst),
                'samt': float(cn_sgst),
                'iamt': float(cn_igst),
                'ntty': 'C',
                'p_gst': 'N',
                'pos': pos_code,
                'itms': cn_items_data,
            })

    # CA AUDIT (Issue 9): Advances on Services (Table 11A 'at' & Table 11B 'atadj')
    at = []
    atadj = []
    advance_payments = Payment.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
        invoice__isnull=True,
        customer__isnull=False,
    ).select_related('customer')

    for p in advance_payments:
        adv_pos_raw = (p.customer.state if p.customer else None) or seller_state_code
        adv_pos = get_pos_state_code(adv_pos_raw, fallback_state=seller_state_code)
        adv_is_inter = (seller_state_code != adv_pos)

        adv_gross = Decimal(str(p.amount or 0))
        if adv_gross > Decimal('0.00'):
            adv_taxable = (adv_gross / Decimal('1.18')).quantize(Decimal('0.01'))
            adv_tax = (adv_gross - adv_taxable).quantize(Decimal('0.01'))
            if adv_is_inter:
                adv_cgst = Decimal('0.00')
                adv_sgst = Decimal('0.00')
                adv_igst = adv_tax
            else:
                adv_cgst = (adv_tax / Decimal('2')).quantize(Decimal('0.01'))
                adv_sgst = adv_tax - adv_cgst
                adv_igst = Decimal('0.00')

            at.append({
                'sply_ty': 'INTER' if adv_is_inter else 'INTRA',
                'pos': adv_pos,
                'rt': 18.0,
                'ad_amt': float(adv_gross),
                'camt': float(adv_cgst),
                'samt': float(adv_sgst),
                'iamt': float(adv_igst),
                'csamt': 0.0,
            })

    # Format HSN Summaries (Split into B2B and B2C)
    hsn_data_b2b = []
    for idx, (hkey, hval) in enumerate(sorted(hsn_dict_b2b.items()), 1):
        hsn_data_b2b.append({
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

    hsn_data_b2c = []
    for idx, (hkey, hval) in enumerate(sorted(hsn_dict_b2c.items()), 1):
        hsn_data_b2c.append({
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

    hsn_data_all = hsn_data_b2b + hsn_data_b2c

    # Documents Issued (Section 13) - clean numbering without duplicate prefixes (Issue 10)
    first_cn = credit_notes.first()
    last_cn = credit_notes.last()
    first_cn_num = re.sub(r'^(CN-)+', 'CN-', first_cn.credit_note_number) if first_cn else ''
    last_cn_num = re.sub(r'^(CN-)+', 'CN-', last_cn.credit_note_number) if last_cn else ''

    doc_issue = {
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
            }
        ]
    }

    gstin = tenant.gstin if hasattr(tenant, 'gstin') and tenant.gstin else ''
    fp = datetime.datetime.strptime(from_date, '%Y-%m-%d').strftime('%m%Y')

    gstr1 = {
        'gstin': gstin,
        'fp': fp,
        'b2b': b2b,
        'b2cs': b2cs,
        'b2cl': b2cl,
        'cdnr': cdnr,
        'cdnur': cdnur,
        'hsn': {
            'data': hsn_data_all,
            'b2b': hsn_data_b2b,
            'b2c': hsn_data_b2c,
        },
        'hsn_b2b': hsn_data_b2b,
        'hsn_b2c': hsn_data_b2c,
        'at': at,
        'atadj': atadj,
        'doc_issue': doc_issue,
    }

    return Response(gstr1)


# ─── GSTR-3B Summary Report ──────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr3b_summary(request):
    """
    Generate GSTR-3B summary computation for a given period.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    Computes:
    - Table 3.1: Outward supplies & Inward reverse charge
    - Table 3.1.1: Supplies notified under Section 9(5)
    - Table 3.2: Interstate supplies to unregistered by POS
    - Table 4: Eligible ITC (Available, Reversed, Net ITC, Ineligible)
    - Table 5: Exempt/Nil inward supplies
    - Table 5.1: Interest & Late fee
    - Table 6.1: Payment of Tax (Net tax payable in cash under GST Rule 88A set-off order)
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')

    if not from_date or not to_date:
        return Response({'error': 'Both from and to dates are required'}, status=400)

    seller_state_code = get_seller_state_code(tenant)

    # 1. Outward Supplies (from finalized Sales Invoices)
    invoices = SalesInvoice.objects.filter(
        created_by=tenant,
        status='final',
        invoice_date__gte=from_date,
        invoice_date__lte=to_date,
    ).select_related('customer').prefetch_related('items__product')

    outward_taxable = Decimal('0.00')
    outward_igst = Decimal('0.00')
    outward_cgst = Decimal('0.00')
    outward_sgst = Decimal('0.00')

    # Table 3.2: Interstate supplies to unregistered persons
    table32_dict = {}

    for inv in invoices:
        items = [i for i in inv.items.all() if getattr(i, 'row_type', 'item') != 'note']
        pos_raw = inv.place_of_supply or (inv.customer.state if inv.customer else None) or seller_state_code
        pos_code = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
        is_inter = (seller_state_code != pos_code)
        has_gstin = bool(inv.customer and inv.customer.gstin and len(inv.customer.gstin.strip()) == 15)

        for item in items:
            taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
            outward_taxable += taxable
            outward_igst += igst
            outward_cgst += cgst
            outward_sgst += sgst

            if is_inter and not has_gstin:
                if pos_code not in table32_dict:
                    table32_dict[pos_code] = {'pos': pos_code, 'txval': Decimal('0.00'), 'iamt': Decimal('0.00')}
                table32_dict[pos_code]['txval'] += taxable
                table32_dict[pos_code]['iamt'] += igst

    # Less Credit Notes (Sales Returns)
    credit_notes = CreditNote.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
    ).select_related('customer', 'original_invoice').prefetch_related('items__product')

    for cn in credit_notes:
        orig_inv = cn.original_invoice
        pos_raw = (cn.customer.state if cn.customer else None) or (orig_inv.place_of_supply if orig_inv else None) or seller_state_code
        pos_code = get_pos_state_code(pos_raw, fallback_state=seller_state_code)
        is_inter = (seller_state_code != pos_code)
        has_gstin = bool(cn.customer and cn.customer.gstin and len(cn.customer.gstin.strip()) == 15)

        for item in cn.items.all():
            taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
            outward_taxable -= taxable
            outward_igst -= igst
            outward_cgst -= cgst
            outward_sgst -= sgst

            if is_inter and not has_gstin and pos_code in table32_dict:
                table32_dict[pos_code]['txval'] -= taxable
                table32_dict[pos_code]['iamt'] -= igst

    # Floor at 0.00
    outward_taxable = max(outward_taxable, Decimal('0.00'))
    outward_igst = max(outward_igst, Decimal('0.00'))
    outward_cgst = max(outward_cgst, Decimal('0.00'))
    outward_sgst = max(outward_sgst, Decimal('0.00'))

    # 2. Inward Supplies / ITC Available (from Purchase Bills)
    bills = PurchaseBill.objects.filter(
        created_by=tenant,
        bill_date__gte=from_date,
        bill_date__lte=to_date,
    ).select_related('vendor').prefetch_related('items__product')

    itc_taxable = Decimal('0.00')
    itc_igst = Decimal('0.00')
    itc_cgst = Decimal('0.00')
    itc_sgst = Decimal('0.00')

    for bill in bills:
        vendor_state = getattr(bill.vendor, 'state', None) if bill.vendor else None
        vendor_pos_code = get_pos_state_code(vendor_state, fallback_state=seller_state_code)
        is_inter = (seller_state_code != vendor_pos_code)

        for item in bill.items.all():
            taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
            itc_taxable += taxable
            itc_igst += igst
            itc_cgst += cgst
            itc_sgst += sgst

    # 3. ITC Reversed (from Debit Notes / Purchase Returns)
    debit_notes = DebitNote.objects.filter(
        created_by=tenant,
        date__gte=from_date,
        date__lte=to_date,
    ).select_related('original_bill').prefetch_related('items__product')

    rev_igst = Decimal('0.00')
    rev_cgst = Decimal('0.00')
    rev_sgst = Decimal('0.00')

    for dn in debit_notes:
        orig_bill = dn.original_bill
        vendor_state = getattr(orig_bill.vendor, 'state', None) if (orig_bill and orig_bill.vendor) else None
        vendor_pos_code = get_pos_state_code(vendor_state, fallback_state=seller_state_code)
        is_inter = (seller_state_code != vendor_pos_code)

        for item in dn.items.all():
            _taxable, tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_inter)
            rev_igst += igst
            rev_cgst += cgst
            rev_sgst += sgst

    # 4. Net ITC Available (Table 4.C = 4.A - 4.B)
    net_itc_igst = max(itc_igst - rev_igst, Decimal('0.00'))
    net_itc_cgst = max(itc_cgst - rev_cgst, Decimal('0.00'))
    net_itc_sgst = max(itc_sgst - rev_sgst, Decimal('0.00'))

    # 5. Table 6.1: Tax Payment & Cash Liability Computation under GST Rule 88A
    # RULE 88A Order:
    # 1. IGST ITC must be fully exhausted first: against IGST liability, then CGST and SGST in order.
    # 2. CGST ITC can then be used against CGST liability, then remaining IGST liability (never SGST).
    # 3. SGST ITC can then be used against SGST liability, then remaining IGST liability (never CGST).
    liab_igst = outward_igst
    liab_cgst = outward_cgst
    liab_sgst = outward_sgst

    rem_itc_igst = net_itc_igst
    rem_itc_cgst = net_itc_cgst
    rem_itc_sgst = net_itc_sgst

    # Step 1: IGST ITC -> IGST liability
    igst_setoff_igst = min(liab_igst, rem_itc_igst)
    liab_igst -= igst_setoff_igst
    rem_itc_igst -= igst_setoff_igst

    # Step 2: Remaining IGST ITC -> CGST liability, then SGST liability
    igst_setoff_cgst = min(liab_cgst, rem_itc_igst)
    liab_cgst -= igst_setoff_cgst
    rem_itc_igst -= igst_setoff_cgst

    igst_setoff_sgst = min(liab_sgst, rem_itc_igst)
    liab_sgst -= igst_setoff_sgst
    rem_itc_igst -= igst_setoff_sgst

    # Step 3: CGST ITC -> CGST liability, then remaining IGST liability
    cgst_setoff_cgst = min(liab_cgst, rem_itc_cgst)
    liab_cgst -= cgst_setoff_cgst
    rem_itc_cgst -= cgst_setoff_cgst

    cgst_setoff_igst = min(liab_igst, rem_itc_cgst)
    liab_igst -= cgst_setoff_igst
    rem_itc_cgst -= cgst_setoff_igst

    # Step 4: SGST ITC -> SGST liability, then remaining IGST liability
    sgst_setoff_sgst = min(liab_sgst, rem_itc_sgst)
    liab_sgst -= sgst_setoff_sgst
    rem_itc_sgst -= sgst_setoff_sgst

    sgst_setoff_igst = min(liab_igst, rem_itc_sgst)
    liab_igst -= sgst_setoff_igst
    rem_itc_sgst -= sgst_setoff_igst

    # Total ITC utilized per tax head
    tot_paid_itc_igst = igst_setoff_igst + cgst_setoff_igst + sgst_setoff_igst
    tot_paid_itc_cgst = igst_setoff_cgst + cgst_setoff_cgst
    tot_paid_itc_sgst = igst_setoff_sgst + sgst_setoff_sgst

    cash_payable_igst = liab_igst
    cash_payable_cgst = liab_cgst
    cash_payable_sgst = liab_sgst
    total_cash_payable = cash_payable_igst + cash_payable_cgst + cash_payable_sgst

    gstin = tenant.gstin if hasattr(tenant, 'gstin') and tenant.gstin else ''
    fp = datetime.datetime.strptime(from_date, '%Y-%m-%d').strftime('%m%Y')

    return Response({
        'gstin': gstin,
        'legal_name': tenant.business_name or tenant.username,
        'fp': fp,
        'from_date': from_date,
        'to_date': to_date,
        'table_3_1': {
            'outward_taxable': {
                'desc': '(a) Outward taxable supplies (other than zero-rated, nil and exempted)',
                'txval': float(outward_taxable),
                'iamt': float(outward_igst),
                'camt': float(outward_cgst),
                'samt': float(outward_sgst),
                'csamt': 0.0,
            },
            'zero_rated': {'desc': '(b) Outward taxable supplies (zero rated)', 'txval': 0.0, 'iamt': 0.0, 'csamt': 0.0},
            'nil_exempt': {'desc': '(c) Other outward supplies (Nil rated, exempted)', 'txval': 0.0},
            'inward_reverse_charge': {'desc': '(d) Inward supplies (liable to reverse charge)', 'txval': 0.0, 'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
            'non_gst_outward': {'desc': '(e) Non-GST outward supplies', 'txval': 0.0},
        },
        'table_3_1_1': {
            'eco_taxable': 0.0,
            'eco_igst': 0.0,
            'eco_cgst': 0.0,
            'eco_sgst': 0.0,
        },
        'table_3_2': [
            {'pos': row['pos'], 'txval': float(row['txval']), 'iamt': float(row['iamt'])}
            for row in table32_dict.values() if row['txval'] > 0
        ],
        'table_4_itc': {
            'itc_available': {
                'import_goods': {'iamt': 0.0, 'csamt': 0.0},
                'import_services': {'iamt': 0.0, 'csamt': 0.0},
                'inward_reverse_charge': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'inward_isd': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'all_other_itc': {
                    'iamt': float(itc_igst),
                    'camt': float(itc_cgst),
                    'samt': float(itc_sgst),
                    'csamt': 0.0,
                },
            },
            'itc_reversed': {
                'as_per_rules': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
                'others': {
                    'iamt': float(rev_igst),
                    'camt': float(rev_cgst),
                    'samt': float(rev_sgst),
                    'csamt': 0.0,
                },
            },
            'net_itc_available': {
                'iamt': float(net_itc_igst),
                'camt': float(net_itc_cgst),
                'samt': float(net_itc_sgst),
                'csamt': 0.0,
            },
            'ineligible_itc': {'iamt': 0.0, 'camt': 0.0, 'samt': 0.0, 'csamt': 0.0},
        },
        'table_5_exempt': {
            'inter_state': 0.0,
            'intra_state': 0.0,
        },
        'table_5_1_interest': {
            'iamt': 0.0,
            'camt': 0.0,
            'samt': 0.0,
            'csamt': 0.0,
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


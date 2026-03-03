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
from .models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Customer
from .models_sidecar import EWayBill

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
    user = request.user
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    report_type = request.query_params.get('type', 'sales')

    if report_type == 'sales':
        items = SalesInvoiceItem.objects.filter(
            sales_invoice__created_by=user
        )
        if from_date:
            items = items.filter(sales_invoice__invoice_date__gte=from_date)
        if to_date:
            items = items.filter(sales_invoice__invoice_date__lte=to_date)
    else:
        items = PurchaseBillItem.objects.filter(
            purchase_bill__created_by=user
        )
        if from_date:
            items = items.filter(purchase_bill__bill_date__gte=from_date)
        if to_date:
            items = items.filter(purchase_bill__bill_date__lte=to_date)

    # Group by HSN code
    hsn_data = items.values(
        'hsn_sac_code'
    ).annotate(
        total_quantity=Sum('quantity'),
        total_taxable_value=Sum(F('amount') - F('tax')),
        total_tax=Sum('tax'),
        total_value=Sum('amount'),
    ).order_by('hsn_sac_code')

    results = []
    grand_taxable = Decimal('0')
    grand_tax = Decimal('0')
    grand_total = Decimal('0')

    for row in hsn_data:
        taxable = row['total_taxable_value'] or Decimal('0')
        tax = row['total_tax'] or Decimal('0')
        total = row['total_value'] or Decimal('0')
        
        # Estimate CGST/SGST split (50-50 of total tax for intra-state)
        half_tax = (tax / 2).quantize(Decimal('0.01'))

        results.append({
            'hsn_code': row['hsn_sac_code'] or 'N/A',
            'quantity': row['total_quantity'] or 0,
            'taxable_value': float(taxable),
            'cgst': float(half_tax),
            'sgst': float(half_tax),
            'igst': 0,  # Will be non-zero for inter-state
            'total_tax': float(tax),
            'total_value': float(total),
        })
        grand_taxable += taxable
        grand_tax += tax
        grand_total += total

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
    user = request.user
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')
    report_type = request.query_params.get('type', 'sales')

    if report_type == 'sales':
        invoices = SalesInvoice.objects.filter(created_by=user).prefetch_related('items')
        if from_date:
            invoices = invoices.filter(invoice_date__gte=from_date)
        if to_date:
            invoices = invoices.filter(invoice_date__lte=to_date)
        invoices = invoices.order_by('-invoice_date')
    else:
        invoices = PurchaseBill.objects.filter(created_by=user).prefetch_related('items')
        if from_date:
            invoices = invoices.filter(bill_date__gte=from_date)
        if to_date:
            invoices = invoices.filter(bill_date__lte=to_date)
        invoices = invoices.order_by('-bill_date')

    results = []
    totals = {'taxable': Decimal('0'), 'cgst': Decimal('0'), 'sgst': Decimal('0'), 'igst': Decimal('0'), 'total': Decimal('0')}

    for inv in invoices:
        items = inv.items.all()
        taxable = sum(i.amount - i.tax for i in items)
        total_tax = sum(i.tax for i in items)

        # Determine intra-state vs inter-state
        is_inter_state = False
        if report_type == 'sales':
            if hasattr(inv, 'place_of_supply') and inv.place_of_supply:
                if hasattr(user, 'state') and user.state and inv.place_of_supply != user.state:
                    is_inter_state = True

        if is_inter_state:
            cgst = Decimal('0')
            sgst = Decimal('0')
            igst = total_tax
        else:
            half = (total_tax / 2).quantize(Decimal('0.01'))
            cgst = half
            sgst = half
            igst = Decimal('0')

        row = {
            'invoice_number': inv.invoice_number if report_type == 'sales' else inv.bill_number,
            'date': str(inv.invoice_date if report_type == 'sales' else inv.bill_date),
            'party_name': (inv.customer_name or (inv.customer.name if inv.customer else 'Cash')) if report_type == 'sales' else inv.vendor_name,
            'gstin': (inv.customer.gstin if inv.customer and inv.customer.gstin else '') if report_type == 'sales' else (inv.vendor_gstin or ''),
            'taxable_value': float(taxable),
            'cgst': float(cgst),
            'sgst': float(sgst),
            'igst': float(igst),
            'total_tax': float(total_tax),
            'total_amount': float(inv.total_amount),
        }
        results.append(row)

        totals['taxable'] += taxable
        totals['cgst'] += cgst
        totals['sgst'] += sgst
        totals['igst'] += igst
        totals['total'] += inv.total_amount

    return Response({
        'type': report_type,
        'from_date': from_date,
        'to_date': to_date,
        'count': len(results),
        'totals': {k: float(v) for k, v in totals.items()},
        'results': results,
    })


# ─── GSTR-1 JSON Export ──────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def gstr1_json_export(request):
    """
    Generate GSTR-1 compliant JSON for a given period.
    Query Params: ?from=YYYY-MM-DD&to=YYYY-MM-DD
    Output JSON follows NIC portal GSTR-1 schema.
    """
    user = request.user
    from_date = request.query_params.get('from')
    to_date = request.query_params.get('to')

    if not from_date or not to_date:
        return Response({'error': 'Both from and to dates are required'}, status=400)

    invoices = SalesInvoice.objects.filter(
        created_by=user,
        invoice_date__gte=from_date,
        invoice_date__lte=to_date,
    ).select_related('customer').prefetch_related('items__product')

    b2b = []  # B2B invoices (with GSTIN)
    b2cs = []  # B2C Small (without GSTIN, < 2.5L)
    b2cl = []  # B2C Large (without GSTIN, >= 2.5L, interstate)

    for inv in invoices:
        items_data = []
        for item in inv.items.all():
            total_tax = item.tax
            is_inter = (inv.place_of_supply and hasattr(user, 'state') and user.state and inv.place_of_supply != user.state)

            if is_inter:
                igst = float(total_tax)
                cgst = sgst = 0
            else:
                half = float((total_tax / 2).quantize(Decimal('0.01')))
                cgst = sgst = half
                igst = 0

            items_data.append({
                'num': item.product.hsn_sac_code or '',
                'itm_det': {
                    'txval': float(item.amount - item.tax),
                    'rt': float(item.product.tax) if hasattr(item.product, 'tax') else 0,
                    'camt': cgst,
                    'samt': sgst,
                    'iamt': igst,
                    'csamt': 0,  # Cess
                }
            })

        has_gstin = inv.customer and inv.customer.gstin

        if has_gstin:
            # B2B
            b2b_entry = {
                'ctin': inv.customer.gstin,
                'inv': [{
                    'inum': inv.invoice_number,
                    'idt': inv.invoice_date.strftime('%d-%m-%Y'),
                    'val': float(inv.total_amount),
                    'pos': inv.place_of_supply or (user.state if hasattr(user, 'state') else ''),
                    'rchrg': 'N',
                    'inv_typ': 'R',
                    'itms': items_data,
                }]
            }
            # Merge into existing GSTIN entry or add new
            existing = next((b for b in b2b if b['ctin'] == inv.customer.gstin), None)
            if existing:
                existing['inv'].append(b2b_entry['inv'][0])
            else:
                b2b.append(b2b_entry)
        else:
            # B2C
            is_inter = (inv.place_of_supply and hasattr(user, 'state') and user.state and inv.place_of_supply != user.state)
            if is_inter and inv.total_amount >= 250000:
                # B2C Large
                b2cl.append({
                    'pos': inv.place_of_supply or '',
                    'inv': [{
                        'inum': inv.invoice_number,
                        'idt': inv.invoice_date.strftime('%d-%m-%Y'),
                        'val': float(inv.total_amount),
                        'itms': items_data,
                    }]
                })
            else:
                # B2C Small — aggregate by state + rate
                for item in items_data:
                    b2cs.append({
                        'sply_ty': 'INTER' if is_inter else 'INTRA',
                        'pos': inv.place_of_supply or (user.state if hasattr(user, 'state') else ''),
                        'rt': item['itm_det']['rt'],
                        'txval': item['itm_det']['txval'],
                        'camt': item['itm_det']['camt'],
                        'samt': item['itm_det']['samt'],
                        'iamt': item['itm_det']['iamt'],
                        'csamt': 0,
                    })

    # HSN Summary
    hsn_items = SalesInvoiceItem.objects.filter(
        sales_invoice__created_by=user,
        sales_invoice__invoice_date__gte=from_date,
        sales_invoice__invoice_date__lte=to_date,
    ).values('hsn_sac_code').annotate(
        total_qty=Sum('quantity'),
        total_val=Sum('amount'),
        total_tax=Sum('tax'),
        taxable_val=Sum(F('amount') - F('tax')),
    )

    hsn_data = []
    for h in hsn_items:
        tax = h['total_tax'] or Decimal('0')
        half = float((tax / 2).quantize(Decimal('0.01')))
        hsn_data.append({
            'num': 1,
            'hsn_sc': h['hsn_sac_code'] or '',
            'qty': h['total_qty'] or 0,
            'val': float(h['total_val'] or 0),
            'txval': float(h['taxable_val'] or 0),
            'camt': half,
            'samt': half,
            'iamt': 0,
            'csamt': 0,
        })

    gstin = user.gstin if hasattr(user, 'gstin') and user.gstin else ''
    fp = datetime.datetime.strptime(from_date, '%Y-%m-%d').strftime('%m%Y')

    gstr1 = {
        'gstin': gstin,
        'fp': fp,
        'b2b': b2b,
        'b2cs': b2cs,
        'b2cl': b2cl,
        'hsn': {'data': hsn_data},
    }

    return Response(gstr1)


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

    try:
        invoice = SalesInvoice.objects.select_related('customer').prefetch_related('items__product').get(
            pk=invoice_id, created_by=request.user
        )
    except SalesInvoice.DoesNotExist:
        return Response({'error': 'Invoice not found'}, status=404)

    user = request.user
    gstin = user.gstin if hasattr(user, 'gstin') and user.gstin else 'UNREGISTERED'

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
            'LglNm': user.business_name or user.username,
            'Addr1': user.business_address[:100] if hasattr(user, 'business_address') and user.business_address else 'N/A',
            'Loc': user.state if hasattr(user, 'state') and user.state else '',
            'Pin': 0,
            'Stcd': user.state if hasattr(user, 'state') and user.state else '',
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

    try:
        invoice = SalesInvoice.objects.select_related('customer').get(
            pk=invoice_id, created_by=request.user
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
            'created_by': request.user,
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


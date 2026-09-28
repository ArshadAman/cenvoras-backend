import json
from decimal import Decimal
from datetime import datetime, date
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Sum, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .models import PurchaseBill, PurchaseBillItem, Vendor
from .models_gst_shield import GSTR2BImport, GSTR2BRecord, VendorLegalNotice
from .services_gstr2b import GSTR2BParser, GSTReconciliationService, LegalNoticeService, calculate_purchase_bill_tax
from .services_ca_pack import CAAuditPackGenerator


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_and_reconcile_gstr2b(request):
    """
    Ingests official GST Portal GSTR-2B file (.json or .xlsx),
    runs high-speed multi-tier reconciliation against Purchase Bills,
    applies payment withholding on delinquent bills, and updates vendor risk scores.
    """
    file_obj = request.FILES.get('file')
    if not file_obj:
        return Response({'error': 'No file uploaded. Please upload a GSTR-2B .json or .xlsx file.'}, status=status.HTTP_400_BAD_REQUEST)

    filename = file_obj.name.lower()
    tenant = getattr(request.user, 'active_tenant', request.user)

    try:
        if filename.endswith('.json'):
            parsed_data = GSTR2BParser.parse_json(file_obj.read())
        elif filename.endswith(('.xlsx', '.xls')):
            parsed_data = GSTR2BParser.parse_excel(file_obj)
        else:
            return Response({'error': 'Unsupported file format. Please upload official GSTR-2B JSON or Excel (.xlsx) file.'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'error': f'Failed to parse GSTR-2B file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

    records_data = parsed_data.get('records', [])
    if not records_data:
        return Response({'error': 'No valid B2B or CDNR invoice records found in uploaded file.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        # Create Import Batch
        import_batch = GSTR2BImport.objects.create(
            return_period=parsed_data.get('return_period', 'UNKNOWN'),
            financial_year=parsed_data.get('financial_year', '2026-2027'),
            source='file_upload',
            file_name=file_obj.name,
            total_invoices=len(records_data),
            total_taxable_value=sum(r['taxable_value'] for r in records_data),
            total_itc_available=sum(r['total_tax'] for r in records_data if r['itc_availability'] == 'Y'),
            total_igst=sum(r['igst'] for r in records_data),
            total_cgst=sum(r['cgst'] for r in records_data),
            total_sgst=sum(r['sgst'] for r in records_data),
            total_cess=sum(r['cess'] for r in records_data),
            created_by=tenant
        )

        # Bulk create GSTR2BRecords
        rec_objs = [
            GSTR2BRecord(
                import_batch=import_batch,
                supplier_gstin=r['supplier_gstin'],
                supplier_trade_name=r.get('supplier_trade_name') or '',
                invoice_number=r['invoice_number'],
                normalized_invoice_number=r['normalized_invoice_number'],
                invoice_type=r.get('invoice_type', 'B2B'),
                invoice_date=r.get('invoice_date'),
                taxable_value=r['taxable_value'],
                igst=r['igst'],
                cgst=r['cgst'],
                sgst=r['sgst'],
                cess=r['cess'],
                total_tax=r['total_tax'],
                itc_availability=r.get('itc_availability', 'Y'),
                filing_date=r.get('filing_date'),
                match_status='missing_in_books',
                created_by=tenant
            )
            for r in records_data
        ]
        GSTR2BRecord.objects.bulk_create(rec_objs)

        # Run Reconciliation
        reconcile_result = GSTReconciliationService.run_reconciliation(request.user, import_batch)

    return Response({
        'message': 'GSTR-2B file ingested and reconciled successfully.',
        'batch_id': str(import_batch.id),
        'return_period': import_batch.return_period,
        'total_invoices_in_2b': import_batch.total_invoices,
        'matched_count': reconcile_result['matched_count'],
        'probable_count': reconcile_result['probable_count'],
        'missing_in_books_count': reconcile_result['missing_in_books_count'],
        'at_risk_bills_count': reconcile_result['at_risk_bills_count']
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_reconciliation_summary(request):
    """
    Returns full GST Shield dashboard summary:
    - Safe ITC (Matched) vs At-Risk ITC (Unmatched)
    - Payment Withheld Pool
    - Vendor Defaulters schedule
    - Recent GSTR-2B imports and invoice mismatch lists
    """
    tenant = getattr(request.user, 'active_tenant', request.user)

    bills = PurchaseBill.objects.filter(
        Q(created_by=tenant) | Q(created_by__parent=tenant)
    ).prefetch_related('items', 'vendor')

    safe_itc = Decimal('0.00')
    at_risk_itc = Decimal('0.00')
    withheld_pool = Decimal('0.00')

    matched_bills = []
    unmatched_bills = []
    probable_bills = []

    for b in bills:
        taxable, tax_amt, cgst, sgst, igst = calculate_purchase_bill_tax(b)
        b_info = {
            'id': str(b.id),
            'bill_number': b.bill_number,
            'bill_date': b.bill_date.strftime('%Y-%m-%d') if b.bill_date else '',
            'vendor_name': b.vendor_name or (b.vendor.name if b.vendor else 'Direct Vendor'),
            'vendor_gstin': b.vendor_gstin or (b.vendor.gstin if b.vendor else ''),
            'vendor_id': str(b.vendor.id) if b.vendor else None,
            'taxable_value': float(taxable),
            'tax_amount': float(tax_amt),
            'total_amount': float(b.total_amount),
            'gstr2b_status': b.gstr2b_status,
            'is_gst_withheld': b.is_gst_withheld,
            'gst_withheld_amount': float(b.gst_withheld_amount),
            'gst_withholding_override': b.gst_withholding_override,
            'vendor_risk_tier': b.vendor.risk_tier if b.vendor else 'defaulter',
            'vendor_compliance_score': b.vendor.compliance_score if b.vendor else 50
        }

        if b.gstr2b_status == 'matched':
            safe_itc += tax_amt
            matched_bills.append(b_info)
        elif b.gstr2b_status == 'probable_match':
            probable_bills.append(b_info)
        else:
            at_risk_itc += tax_amt
            unmatched_bills.append(b_info)

        if b.is_gst_withheld:
            withheld_pool += b.gst_withheld_amount

    # Missing in books (in 2B but not in ERP)
    missing_in_books_qs = GSTR2BRecord.objects.filter(
        created_by=tenant,
        match_status='missing_in_books'
    )[:50]

    missing_in_books = [
        {
            'id': str(r.id),
            'supplier_gstin': r.supplier_gstin,
            'supplier_name': r.supplier_trade_name,
            'invoice_number': r.invoice_number,
            'invoice_date': r.invoice_date.strftime('%Y-%m-%d') if r.invoice_date else '',
            'taxable_value': float(r.taxable_value),
            'total_tax': float(r.total_tax),
            'itc_availability': r.itc_availability
        }
        for r in missing_in_books_qs
    ]

    # Delinquent Vendors summary
    defaulter_vendors = Vendor.objects.filter(
        Q(created_by=tenant) | Q(created_by__parent=tenant),
        total_at_risk_itc__gt=0
    ).order_by('-total_at_risk_itc')[:10]

    defaulters_list = [
        {
            'id': str(v.id),
            'name': v.name,
            'gstin': v.gstin or 'N/A',
            'phone': v.phone or '',
            'compliance_score': v.compliance_score,
            'risk_tier': v.risk_tier,
            'at_risk_itc': float(v.total_at_risk_itc),
            'total_billed_itc': float(v.total_billed_itc)
        }
        for v in defaulter_vendors
    ]

    # Recent Import history
    recent_imports = GSTR2BImport.objects.filter(created_by=tenant)[:5]
    imports_list = [
        {
            'id': str(imp.id),
            'return_period': imp.return_period,
            'file_name': imp.file_name,
            'reconciled_at': imp.reconciled_at.strftime('%d-%b-%Y %H:%M'),
            'total_invoices': imp.total_invoices,
            'matched_count': imp.matched_count,
            'missing_in_books_count': imp.missing_in_books_count
        }
        for imp in recent_imports
    ]

    return Response({
        'safe_itc': float(safe_itc),
        'at_risk_itc': float(at_risk_itc),
        'withheld_pool': float(withheld_pool),
        'counts': {
            'total_bills': len(bills),
            'matched': len(matched_bills),
            'unmatched': len(unmatched_bills),
            'probable': len(probable_bills),
            'missing_in_books': missing_in_books_qs.count()
        },
        'unmatched_bills': unmatched_bills,
        'probable_bills': probable_bills,
        'missing_in_books': missing_in_books,
        'defaulters': defaulters_list,
        'recent_imports': imports_list
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_bill_withholding(request):
    """Overrides or restores GST payment withholding on a specific PurchaseBill"""
    bill_id = request.data.get('bill_id')
    override = request.data.get('override', True)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        bill = PurchaseBill.objects.get(id=bill_id, created_by=tenant)
    except PurchaseBill.DoesNotExist:
        return Response({'error': 'Purchase bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    bill.gst_withholding_override = bool(override)
    if override:
        bill.is_gst_withheld = False
    else:
        # Re-lock if not matched in 2B
        if bill.gstr2b_status != 'matched':
            _, tax_amt, _, _, _ = calculate_purchase_bill_tax(bill)
            bill.is_gst_withheld = True
            bill.gst_withheld_amount = tax_amt

    bill.save(update_fields=['gst_withholding_override', 'is_gst_withheld', 'gst_withheld_amount'])
    return Response({
        'message': f"Withholding {'overridden (released)' if override else 're-locked'} successfully.",
        'is_gst_withheld': bill.is_gst_withheld,
        'gst_withheld_amount': float(bill.gst_withheld_amount)
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manual_match_record(request):
    """Manually links a GSTR2BRecord with a PurchaseBill"""
    record_id = request.data.get('record_id')
    bill_id = request.data.get('bill_id')

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        record = GSTR2BRecord.objects.get(id=record_id, created_by=tenant)
        bill = PurchaseBill.objects.get(id=bill_id, created_by=tenant)
    except (GSTR2BRecord.DoesNotExist, PurchaseBill.DoesNotExist):
        return Response({'error': 'Record or Purchase bill not found.'}, status=status.HTTP_404_NOT_FOUND)

    record.match_status = 'matched'
    record.matched_purchase_bill = bill
    record.save(update_fields=['match_status', 'matched_purchase_bill'])

    bill.gstr2b_status = 'matched'
    bill.is_gst_withheld = False
    bill.gst_withheld_amount = Decimal('0.00')
    bill.save(update_fields=['gstr2b_status', 'is_gst_withheld', 'gst_withheld_amount'])

    # Recalculate vendor scores
    GSTReconciliationService.update_vendor_compliance_scores(tenant)

    return Response({'message': 'Invoices matched successfully. Withholding unlocked.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_legal_notice(request):
    """Generates statutory legal demand notice against a delinquent vendor"""
    vendor_id = request.data.get('vendor_id')
    deadline_days = int(request.data.get('deadline_days', 7))
    period = request.data.get('period', 'Current Financial Year')

    if not vendor_id:
        return Response({'error': 'Vendor ID is required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        notice_data = LegalNoticeService.generate_notice(
            user=request.user,
            vendor_id=vendor_id,
            period=period,
            deadline_days=deadline_days
        )
        return Response(notice_data)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_ca_audit_pack(request):
    """
    Multi-format CA Audit Pack download:
    - export=xlsx (Multi-tab Excel workbook)
    - export=pdf (Executive printable PDF report)
    - export=csv (Flat CSV registers)
    - export=json (Government Portal payload)
    """
    try:
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        export_format = (request.query_params.get('export') or 'xlsx').lower().strip()

        audit_data = CAAuditPackGenerator.get_audit_data(request.user, from_date, to_date)
        period_slug = f"{from_date or 'start'}_to_{to_date or 'today'}".replace('/', '-')

        if export_format == 'csv':
            csv_content = CAAuditPackGenerator.generate_csv(audit_data, report_type=request.query_params.get('type', 'sales'))
            response = HttpResponse(csv_content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="CA_Report_{period_slug}.csv"'
            return response

        elif export_format == 'json':
            json_content = CAAuditPackGenerator.generate_json(audit_data)
            response = HttpResponse(json_content, content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="GSTR_Portal_Payload_{period_slug}.json"'
            return response

        elif export_format == 'pdf':
            pdf_bytes = CAAuditPackGenerator.generate_pdf(audit_data)
            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="CA_Executive_Report_{period_slug}.pdf"'
            return response

        else:
            # Default: .xlsx multi-tab Excel
            excel_bytes = CAAuditPackGenerator.generate_excel(audit_data)
            response = HttpResponse(
                excel_bytes,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="CA_Audit_Pack_{period_slug}.xlsx"'
            return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({'error': f'Failed to generate CA pack: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

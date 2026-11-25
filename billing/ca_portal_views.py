"""
Public, Magic-Link Token-Authenticated API Views for the CA Auditor Portal.
Allows Chartered Accountants to inspect period GST/Trial Balance summaries
and download Tally XML, GSTR Excel, or the full Audit Pack ZIP without a password.
"""
from datetime import date, datetime
from decimal import Decimal
import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ledger.services import AccountingService
from .ca_pack import generate_ca_audit_pack_zip
from .models_ca import CAMappingProfile
from .services_ca_pack import CAAuditPackGenerator, parse_date_safely
from .tally_exporter import TallyXMLExporter

logger = logging.getLogger(__name__)


def _resolve_ca_profile(request):
    """
    Validate magic_link_token from query params or Authorization/X-CA-Token header.
    Returns (profile, error_response).
    """
    token = (
        request.query_params.get('token')
        or request.headers.get('X-CA-Token')
        or ''
    ).strip()

    if not token:
        return None, Response(
            {'error': 'Missing CA portal access token.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    profile = CAMappingProfile.objects.select_related('tenant').filter(
        magic_link_token=token
    ).first()

    if not profile:
        return None, Response(
            {'error': 'Invalid CA portal link. Please request a new link from your client.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not profile.is_magic_link_valid():
        return None, Response(
            {'error': 'This CA portal magic link has expired. Please request a refreshed link from your client.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    return profile, None


def _parse_bool(val, default=False):
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('1', 'true', 'yes')


@api_view(['GET'])
@permission_classes([AllowAny])
def ca_portal_summary_view(request):
    """
    GET /api/billing/ca-portal/summary/?token=<token>&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
    Returns business identity, GST summary, and Trial Balance integrity for the CA Portal.
    """
    profile, err_resp = _resolve_ca_profile(request)
    if err_resp:
        return err_resp

    tenant = profile.tenant
    today = date.today()
    start_date = parse_date_safely(
        request.query_params.get('start_date') or request.query_params.get('from'),
        date(today.year, today.month, 1),
    )
    end_date = parse_date_safely(
        request.query_params.get('end_date') or request.query_params.get('to'),
        today,
    )

    try:
        audit_data = CAAuditPackGenerator.get_audit_data(tenant, start_date, end_date)
        audit_summary = audit_data.get('summary', {})

        sales_tax = Decimal(str(audit_summary.get('sales_tax', 0)))
        purchase_tax = Decimal(str(audit_summary.get('safe_itc', 0))) + Decimal(str(audit_summary.get('at_risk_itc', 0)))
        purchase_taxable = Decimal(str(audit_summary.get('purchases_taxable', 0)))
        purchase_total = purchase_taxable + purchase_tax

        AccountingService.get_or_create_default_accounts(tenant)
        tb_data = AccountingService.get_trial_balance(tenant, date_to=end_date)
        total_dr = Decimal(str(tb_data.get('total_debits') or 0))
        total_cr = Decimal(str(tb_data.get('total_credits') or 0))

        return Response({
            'business': {
                'name': getattr(tenant, 'business_name', '') or getattr(tenant, 'username', ''),
                'gstin': getattr(tenant, 'gstin', '') or '',
                'state': getattr(tenant, 'state', '') or '',
                'pan_number': getattr(tenant, 'pan_number', '') or '',
            },
            'ca': {
                'name': profile.ca_name,
                'firm_name': profile.ca_firm_name,
                'email': profile.ca_email,
                'preset': profile.preset,
                'expires_at': profile.magic_link_expires_at.isoformat() if profile.magic_link_expires_at else None,
            },
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'summary': {
                'sales_count': len(audit_data.get('sales_rows', [])),
                'sales_taxable': float(audit_summary.get('sales_taxable', 0)),
                'sales_tax': float(sales_tax),
                'sales_total': float(audit_summary.get('sales_total', 0)),
                'purchase_count': len(audit_data.get('purchase_rows', [])),
                'purchase_taxable': float(purchase_taxable),
                'purchase_tax': float(purchase_tax),
                'purchase_total': float(purchase_total),
                'credit_note_count': len(audit_data.get('defaulter_rows', [])),
                'net_gst_payable': float(audit_summary.get('net_tax_payable', 0)),
            },
            'trial_balance': {
                'total_debits': float(total_dr),
                'total_credits': float(total_cr),
                'is_balanced': abs(total_dr - total_cr) < Decimal('0.01'),
            },
        })
    except Exception as e:
        logger.exception("CA Portal summary error for tenant %s: %s", tenant.id, e)
        return Response(
            {'error': f'Failed to load CA Portal summary: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
@permission_classes([AllowAny])
def ca_portal_download_xml_view(request):
    """GET /api/billing/ca-portal/download-xml/?token=<token>&start_date=...&end_date=...&mode=..."""
    profile, err_resp = _resolve_ca_profile(request)
    if err_resp:
        return err_resp

    tenant = profile.tenant
    today = date.today()
    start_date = parse_date_safely(request.query_params.get('start_date'), date(today.year, today.month, 1))
    end_date = parse_date_safely(request.query_params.get('end_date'), today)
    mode = request.query_params.get('mode', 'accounting_only')
    if mode not in ('accounting_only', 'full_inventory'):
        mode = 'accounting_only'

    xml_content = TallyXMLExporter(
        tenant=tenant,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
    ).generate_xml()

    biz_name = getattr(tenant, 'business_name', '') or 'Company'
    safe_name = "".join(c for c in biz_name if c.isalnum() or c in ('-', '_')).strip() or 'Tally'
    filename = f"{safe_name}_Tally_{mode}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xml"

    response = HttpResponse(xml_content, content_type='application/xml; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return response


@api_view(['GET'])
@permission_classes([AllowAny])
def ca_portal_download_zip_view(request):
    """GET /api/billing/ca-portal/download-zip/?token=<token>&start_date=...&end_date=..."""
    profile, err_resp = _resolve_ca_profile(request)
    if err_resp:
        return err_resp

    tenant = profile.tenant
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    include_inventory_xml = _parse_bool(request.query_params.get('include_inventory_xml'), default=False)

    zip_bytes, filename = generate_ca_audit_pack_zip(
        tenant=tenant,
        start_date=start_date,
        end_date=end_date,
        include_inventory_xml=include_inventory_xml,
    )
    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return response


@api_view(['GET'])
@permission_classes([AllowAny])
def ca_portal_download_excel_view(request):
    """GET /api/billing/ca-portal/download-excel/?token=<token>&start_date=...&end_date=..."""
    profile, err_resp = _resolve_ca_profile(request)
    if err_resp:
        return err_resp

    tenant = profile.tenant
    today = date.today()
    start_date = parse_date_safely(request.query_params.get('start_date'), date(today.year, today.month, 1))
    end_date = parse_date_safely(request.query_params.get('end_date'), today)

    audit_data = CAAuditPackGenerator.get_audit_data(tenant, start_date, end_date)
    excel_bytes = CAAuditPackGenerator.generate_excel(audit_data)

    biz_name = getattr(tenant, 'business_name', '') or 'Company'
    safe_name = "".join(c for c in biz_name if c.isalnum() or c in ('-', '_')).strip() or 'Company'
    filename = f"{safe_name}_GSTR_Registers_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"

    response = HttpResponse(
        excel_bytes,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Access-Control-Expose-Headers'] = 'Content-Disposition'
    return response

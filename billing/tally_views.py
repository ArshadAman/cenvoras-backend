"""
API Views for TallyPrime XML Export & CA Interoperability.
"""
from datetime import datetime
import json
import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .tally_exporter import TallyXMLExporter

logger = logging.getLogger(__name__)


def _parse_bool(val, default=True):
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('1', 'true', 'yes')


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def export_tally_xml_view(request):
    """
    Generate and download certified TallyPrime XML package.
    Supports:
    - Mode toggle: 'accounting_only' (default) vs 'full_inventory'
    - Date filtering: start_date, end_date (YYYY-MM-DD)
    - Section toggles: include_sales, include_purchases, include_returns
    - CA Mapping Profile customizations (via POST payload or query params)
    """
    tenant = getattr(request.user, 'active_tenant', request.user)

    params = request.query_params if request.method == 'GET' else request.data

    start_date_str = params.get('start_date')
    end_date_str = params.get('end_date')
    mode = params.get('mode', 'accounting_only')
    if mode not in ('accounting_only', 'full_inventory'):
        mode = 'accounting_only'

    include_sales = _parse_bool(params.get('include_sales'), default=True)
    include_purchases = _parse_bool(params.get('include_purchases'), default=True)
    include_returns = _parse_bool(params.get('include_returns'), default=True)

    ca_mapping = params.get('ca_mapping')
    if isinstance(ca_mapping, str):
        try:
            ca_mapping = json.loads(ca_mapping)
        except Exception:
            ca_mapping = None

    start_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Invalid start_date format. Expected YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    end_date = None
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Invalid end_date format. Expected YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        exporter = TallyXMLExporter(
            tenant=tenant,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            ca_mapping=ca_mapping,
            include_sales=include_sales,
            include_purchases=include_purchases,
            include_returns=include_returns,
        )
        xml_content = exporter.generate_xml()

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        biz_name = getattr(tenant, 'business_name', '') or 'Company'
        safe_biz_name = "".join(c for c in biz_name if c.isalnum() or c in ('-', '_')).strip() or 'Tally'
        filename = f"{safe_biz_name}_Tally_Export_{mode}_{timestamp_str}.xml"

        response = HttpResponse(xml_content, content_type='application/xml; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    except Exception as e:
        logger.exception("Failed to generate Tally XML for tenant %s: %s", tenant.id, e)
        return Response(
            {'error': f'Failed to generate Tally XML: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

"""
API Views for TallyPrime XML Export & CA Interoperability.
"""
from datetime import datetime
import json
import logging

from django.http import HttpResponse
from rest_framework import serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_ca import CAMappingProfile, CAPresetChoices, PRESET_CONFIGURATIONS
from .tally_exporter import TallyXMLExporter

logger = logging.getLogger(__name__)


class CAMappingProfileSerializer(serializers.ModelSerializer):
    is_magic_link_valid = serializers.BooleanField(read_only=True)
    magic_link_url = serializers.SerializerMethodField(read_only=True)
    available_presets = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CAMappingProfile
        fields = [
            'id',
            'ca_name',
            'ca_email',
            'ca_phone',
            'ca_firm_name',
            'magic_link_token',
            'magic_link_expires_at',
            'is_magic_link_valid',
            'magic_link_url',
            'preset',
            'available_presets',
            'sales_account',
            'interstate_sales_account',
            'purchase_account',
            'interstate_purchase_account',
            'sales_return_account',
            'purchase_return_account',
            'cgst_output_prefix',
            'sgst_output_prefix',
            'igst_output_prefix',
            'cgst_input_prefix',
            'sgst_input_prefix',
            'igst_input_prefix',
            'round_off_account',
            'split_tax_by_rate',
            'separate_interstate_sales',
            'separate_interstate_purchases',
            'custom_ledger_overrides',
            'updated_at',
        ]
        read_only_fields = ['id', 'magic_link_token', 'magic_link_expires_at', 'updated_at']

    def get_magic_link_url(self, obj):
        if obj.is_magic_link_valid():
            return f"/ca-portal?token={obj.magic_link_token}"
        return None

    def get_available_presets(self, obj):
        return [
            {'code': choice[0], 'label': choice[1]}
            for choice in CAPresetChoices.choices
        ]


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def ca_profile_detail_view(request):
    """
    GET: Retrieve the active tenant's CA mapping profile (creates default if missing).
    PUT: Update mapping configuration, preset, or CA contact info.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    profile, _ = CAMappingProfile.objects.get_or_create(
        tenant=tenant,
        defaults={'preset': CAPresetChoices.STANDARD_TALLY},
    )

    if request.method == 'GET':
        serializer = CAMappingProfileSerializer(profile)
        return Response(serializer.data)

    elif request.method == 'PUT':
        serializer = CAMappingProfileSerializer(profile, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ca_profile_reset_defaults_view(request):
    """
    Reset profile fields to a selected preset ('standard_tally', 'detailed_slabs', 'simplified_gst').
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    profile, _ = CAMappingProfile.objects.get_or_create(tenant=tenant)

    preset_name = request.data.get('preset', CAPresetChoices.STANDARD_TALLY)
    if preset_name not in PRESET_CONFIGURATIONS:
        return Response(
            {'error': f"Invalid preset '{preset_name}'. Choose from: {list(PRESET_CONFIGURATIONS.keys())}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile.apply_preset(preset_name, save=True)
    serializer = CAMappingProfileSerializer(profile)
    return Response({
        'message': f"Successfully reset CA mapping to '{preset_name}' preset.",
        'profile': serializer.data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ca_profile_generate_magic_link_view(request):
    """
    Generate or renew a secure 30-day passwordless magic-link token for the CA Auditor Portal.
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    profile, _ = CAMappingProfile.objects.get_or_create(tenant=tenant)

    days = int(request.data.get('days', 30))
    token = profile.generate_magic_link_token(days=days)
    magic_url = f"/ca-portal?token={token}"

    return Response({
        'success': True,
        'message': f"Generated CA Portal magic link valid for {days} days.",
        'magic_link_token': token,
        'magic_link_url': magic_url,
        'expires_at': profile.magic_link_expires_at.isoformat(),
    })


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


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ca_audit_pack_zip_view(request):
    """
    1-Click Monthly CA Audit Pack ZIP download.
    Bundles Tally XML(s), GSTR-1/2B Excel, Trial Balance Excel, GSTR JSON, and README_FOR_CA.txt.
    Query params:
    - start_date (optional, YYYY-MM-DD)
    - end_date (optional, YYYY-MM-DD)
    - include_inventory_xml (optional, 'true'/'false', default 'false')
    """
    from .ca_pack import generate_ca_audit_pack_zip

    tenant = getattr(request.user, 'active_tenant', request.user)
    start_date = request.query_params.get('start_date') or request.query_params.get('from')
    end_date = request.query_params.get('end_date') or request.query_params.get('to')
    include_inventory_xml = _parse_bool(request.query_params.get('include_inventory_xml'), default=False)

    try:
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
    except Exception as e:
        logger.exception("Failed to generate CA Audit Pack ZIP for tenant %s: %s", tenant.id, e)
        return Response(
            {'error': f'Failed to generate CA Audit Pack ZIP: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

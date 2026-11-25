"""
API Views for Marg ERP 9+ & TallyPrime Migration Pipeline.
Exposes:
- POST /api/users/migration/marg-preview/  (Tier 2 visual column mapper preview)
- POST /api/users/migration/marg-dry-run/  (Stage 1 + Stage 2 dry-run simulation)
- POST /api/users/migration/marg-commit/   (Stage 3 atomic commit with rollback)
"""
import json
import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .backup_service import generate_complete_business_backup_zip

from .marg_dbf_parser import parse_marg_dbf_zip
from .marg_excel_parser import parse_marg_spreadsheet, preview_marg_spreadsheet
from .migration_service import MigrationBlockerError, MigrationValidationGate
from .tally_importer import parse_tally_xml

logger = logging.getLogger(__name__)


def _parse_optional_json(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def marg_preview_view(request):
    """
    Tier 2 Preview: Upload a Marg .xlsx or .csv file to inspect headers,
    detected entity type, suggested column mappings, and first 10 rows.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        preview = preview_marg_spreadsheet(uploaded.read(), uploaded.name)
        return Response(preview)
    except Exception as e:
        logger.exception("Marg preview failed: %s", e)
        return Response({'error': f'Failed to parse spreadsheet: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def marg_dry_run_view(request):
    """
    Stage 1 & Stage 2: Parse uploaded Marg spreadsheet(s) and execute in-memory dry-run.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    sheet_type = request.data.get('sheet_type') or None
    custom_mapping = _parse_optional_json(request.data.get('custom_mapping'))

    try:
        parsed = parse_marg_spreadsheet(
            uploaded.read(),
            uploaded.name,
            sheet_type=sheet_type,
            custom_mapping=custom_mapping,
        )
        gate = MigrationValidationGate(tenant=tenant)
        report = gate.dry_run(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
        )
        return Response(report.to_dict())
    except Exception as e:
        logger.exception("Marg dry-run failed: %s", e)
        return Response({'error': f'Dry-run failed: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def marg_commit_view(request):
    """
    Stage 3: Parse, validate, and atomically commit Marg spreadsheet data to PostgreSQL.
    Returns 422 Unprocessable Entity with blocker details if any critical blockers exist.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    sheet_type = request.data.get('sheet_type') or None
    custom_mapping = _parse_optional_json(request.data.get('custom_mapping'))

    try:
        parsed = parse_marg_spreadsheet(
            uploaded.read(),
            uploaded.name,
            sheet_type=sheet_type,
            custom_mapping=custom_mapping,
        )
        gate = MigrationValidationGate(tenant=tenant)
        result = gate.commit(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
            historical_vouchers=parsed.get('historical_vouchers'),
        )
        return Response(result, status=status.HTTP_200_OK)
    except MigrationBlockerError as mbe:
        return Response(
            {
                'success': False,
                'error': str(mbe),
                'blockers': mbe.blockers,
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except Exception as e:
        logger.exception("Marg commit failed: %s", e)
        return Response({'error': f'Commit failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def marg_dbf_dry_run_view(request):
    """
    Tier 3 DBF Dry-Run: Upload a zipped Marg DATA folder (.zip containing .DBF files)
    and run Stage 1 + Stage 2 validation without writing to the database.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No ZIP file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        parsed = parse_marg_dbf_zip(uploaded.read())
        gate = MigrationValidationGate(tenant=tenant)
        report = gate.dry_run(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
        )
        res = report.to_dict()
        res['detected_files'] = parsed['detected_files']
        return Response(res)
    except Exception as e:
        logger.exception("Marg DBF dry-run failed: %s", e)
        return Response({'error': f'DBF dry-run failed: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def marg_dbf_commit_view(request):
    """
    Tier 3 DBF Commit: Upload a zipped Marg DATA folder and atomically commit to PostgreSQL.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No ZIP file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        parsed = parse_marg_dbf_zip(uploaded.read())
        gate = MigrationValidationGate(tenant=tenant)
        result = gate.commit(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
            historical_vouchers=parsed.get('historical_vouchers'),
        )
        result['detected_files'] = parsed['detected_files']
        return Response(result, status=status.HTTP_200_OK)
    except MigrationBlockerError as mbe:
        return Response(
            {'success': False, 'error': str(mbe), 'blockers': mbe.blockers},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except Exception as e:
        logger.exception("Marg DBF commit failed: %s", e)
        return Response({'error': f'DBF commit failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def tally_dry_run_view(request):
    """
    TallyPrime XML Dry-Run: Upload Master.xml or DayBook.xml and run
    Stage 1 + Stage 2 validation without writing to PostgreSQL.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No XML file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        parsed = parse_tally_xml(uploaded.read())
        gate = MigrationValidationGate(tenant=tenant)
        report = gate.dry_run(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
        )
        res = report.to_dict()
        res['historical_vouchers_count'] = len(parsed['historical_vouchers'])
        return Response(res)
    except Exception as e:
        logger.exception("Tally XML dry-run failed: %s", e)
        return Response({'error': f'Tally XML dry-run failed: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def tally_commit_view(request):
    """
    TallyPrime XML Commit: Upload Master.xml / DayBook.xml and atomically commit
    masters and opening stock to PostgreSQL.
    """
    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'No XML file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = getattr(request.user, 'active_tenant', request.user)
    try:
        parsed = parse_tally_xml(uploaded.read())
        gate = MigrationValidationGate(tenant=tenant)
        result = gate.commit(
            products=parsed['products'],
            parties=parsed['parties'],
            batches=parsed['batches'],
            historical_vouchers=parsed.get('historical_vouchers'),
        )
        result['historical_vouchers_count'] = len(parsed['historical_vouchers'])
        return Response(result, status=status.HTTP_200_OK)
    except MigrationBlockerError as mbe:
        return Response(
            {'success': False, 'error': str(mbe), 'blockers': mbe.blockers},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except Exception as e:
        logger.exception("Tally XML commit failed: %s", e)
        return Response({'error': f'Tally XML commit failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def complete_backup_zip_view(request):
    """
    Task 3.1: 1-Click Complete Business Backup ZIP download for Data Sovereignty.
    """
    try:
        zip_bytes, filename = generate_complete_business_backup_zip(request.user)
        response = HttpResponse(zip_bytes, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response
    except Exception as e:
        logger.exception("Complete backup ZIP generation failed: %s", e)
        return Response({'error': f'Backup failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

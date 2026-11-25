"""
Read-Only API Views for HistoricalDocumentArchive (billing/archive_views.py).
Allows migrated Tally/Marg users to search and view/reprint past invoices.
"""
from django.db.models import Q
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_archive import HistoricalDocumentArchive


class HistoricalDocumentArchiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoricalDocumentArchive
        fields = [
            'id',
            'source_erp',
            'voucher_type',
            'document_number',
            'document_date',
            'party_name',
            'party_gstin',
            'total_amount',
            'items',
            'raw_metadata',
            'created_at',
        ]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def historical_archive_list_view(request):
    """
    GET /api/billing/archive/documents/?q=&voucher_type=&start_date=&end_date=
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    qs = HistoricalDocumentArchive.objects.filter(tenant=tenant)

    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(document_number__icontains=q) | Q(party_name__icontains=q)
        )

    vch_type = (request.query_params.get('voucher_type') or '').strip()
    if vch_type:
        qs = qs.filter(voucher_type__iexact=vch_type)

    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if start_date:
        qs = qs.filter(document_date__gte=start_date)
    if end_date:
        qs = qs.filter(document_date__lte=end_date)

    limit = min(int(request.query_params.get('limit', 100)), 500)
    serializer = HistoricalDocumentArchiveSerializer(qs[:limit], many=True)
    return Response({
        'count': qs.count(),
        'results': serializer.data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def historical_archive_detail_view(request, pk):
    """
    GET /api/billing/archive/documents/<uuid:pk>/
    """
    tenant = getattr(request.user, 'active_tenant', request.user)
    doc = HistoricalDocumentArchive.objects.filter(tenant=tenant, pk=pk).first()
    if not doc:
        return Response({'error': 'Archived document not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(HistoricalDocumentArchiveSerializer(doc).data)

"""RCM self-invoice / payment voucher endpoints (see billing/rcm_documents.py)."""
from datetime import date as _Date

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from billing.models import PurchaseBill
from billing.models_rcm import RcmPaymentVoucher, RcmSelfInvoice
from billing.rcm_documents import (RcmDocumentError, create_self_invoice,
                                   self_invoices_due)


class RcmSelfInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = RcmSelfInvoice
        fields = ['id', 'purchase_bill', 'number', 'date', 'receipt_date', 'supplier_name', 'supplier_address',
                  'supplier_state', 'place_of_supply', 'taxable_value', 'cgst', 'sgst', 'igst', 'created_at']


class RcmPaymentVoucherSerializer(serializers.ModelSerializer):
    class Meta:
        model = RcmPaymentVoucher
        fields = ['id', 'purchase_bill', 'number', 'date', 'supplier_name', 'amount_paid', 'payment_mode',
                  'payment_reference', 'taxable_value',
                  'cgst', 'sgst', 'igst', 'created_at']


def _tenant(request):
    return getattr(request.user, 'active_tenant', None) or request.user


def _date(value, default):
    if not value:
        return default
    try:
        return _Date.fromisoformat(str(value))
    except ValueError:
        raise RcmDocumentError(f'Invalid date "{value}" (use YYYY-MM-DD).')


def _bill(tenant, pk):
    return PurchaseBill.objects.filter(created_by=tenant, pk=pk).prefetch_related('items').select_related('vendor').first()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def rcm_self_invoice_create(request, pk):
    tenant = _tenant(request)
    bill = _bill(tenant, pk)
    if bill is None:
        return Response({'error': 'Purchase bill not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        doc = create_self_invoice(tenant, bill, _date(request.data.get('date'), _date_today()))
    except RcmDocumentError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(RcmSelfInvoiceSerializer(doc).data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rcm_self_invoice_list(request):
    tenant = _tenant(request)
    return Response({'results': RcmSelfInvoiceSerializer(RcmSelfInvoice.objects.filter(created_by=tenant), many=True).data,
                     'due': self_invoices_due(tenant, _date_today())})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def rcm_payment_voucher_list(request):
    tenant = _tenant(request)
    return Response({'results': RcmPaymentVoucherSerializer(
        RcmPaymentVoucher.objects.filter(created_by=tenant), many=True).data})


def _date_today():
    from billing.advances import today_ist
    return today_ist()

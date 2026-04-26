import os
import tempfile

from celery.result import AsyncResult
from django.http import FileResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .tasks import generate_sales_invoice_csv, process_sales_invoice_csv


def _csv_job_status(task_id):
    task = AsyncResult(task_id)
    payload = {
        'task_id': task_id,
        'state': task.state,
        'ready': task.ready(),
    }
    if task.state == 'SUCCESS':
        payload['result'] = task.result
    elif task.state == 'FAILURE':
        payload['error'] = str(task.result)
    return payload


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_sales_invoices_csv(request):
    filters = {key: request.query_params.get(key, '') for key in request.query_params.keys()}
    task = generate_sales_invoice_csv.delay(str(request.user.id), filters)
    return Response({
        'success': True,
        'message': 'Sales CSV export queued in the background.',
        'task_id': task.id,
        'status_url': f'/api/billing/sales-invoices/csv-jobs/{task.id}/',
        'download_url': f'/api/billing/sales-invoices/csv-jobs/{task.id}/download/',
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sales_csv_job_status(request, task_id):
    return Response(_csv_job_status(task_id))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_sales_csv(request, task_id):
    task = AsyncResult(task_id)
    if task.state == 'PENDING':
        return Response({'success': False, 'message': 'Export is still processing.'}, status=status.HTTP_202_ACCEPTED)
    if task.state == 'FAILURE':
        return Response({'success': False, 'message': 'Export failed.', 'error': str(task.result)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    result = task.result or {}
    file_path = result.get('file_path')
    filename = result.get('filename', f'sales-invoices-{task_id}.csv')
    if not file_path or not os.path.exists(file_path):
        return Response({'success': False, 'message': 'Export file is missing.'}, status=status.HTTP_404_NOT_FOUND)

    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_sales_invoices_csv(request):
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return Response({'error': 'CSV file is required using form key "file".'}, status=status.HTTP_400_BAD_REQUEST)

    if not uploaded_file.name.lower().endswith('.csv'):
        return Response({'error': 'Only CSV files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
        temp_file.close()
    except Exception:
        return Response({'error': 'Unable to store CSV file for background processing.'}, status=status.HTTP_400_BAD_REQUEST)

    task = process_sales_invoice_csv.delay(temp_file.name, str(request.user.id))
    return Response({
        'success': True,
        'message': 'Sales CSV import queued in the background.',
        'task_id': task.id,
        'status_url': f'/api/billing/sales-invoices/csv-jobs/{task.id}/',
    }, status=status.HTTP_202_ACCEPTED)
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services import get_stock_valuation, get_expiry_report, get_item_wise_profit
from django.utils import timezone
import datetime

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stock_valuation_view(request):
    """
    Get current stock valuation.
    """
    data = get_stock_valuation()
    return Response(data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def expiry_report_view(request):
    """
    Get expiry report.
    Query Params: ?days=30 (default)
    """
    days = int(request.query_params.get('days', 30))
    data = get_expiry_report(days_threshold=days)
    return Response(data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profit_loss_view(request):
    """
    Get Item-Wise Profit & Loss.
    Query Params: ?start_date=YYYY-MM-DD & ?end_date=YYYY-MM-DD
    """
    today = timezone.now().date()
    start_date_str = request.query_params.get('start_date')
    end_date_str = request.query_params.get('end_date')
    
    start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today.replace(day=1)
    end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
    
    data = get_item_wise_profit(start_date, end_date)
    return Response(data)

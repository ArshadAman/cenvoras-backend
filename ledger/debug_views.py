from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from billing.models import PurchaseBill
from ledger.services import AccountingService
from datetime import date


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def test_purchase_bill_accounting(request):
    """Test endpoint to debug purchase bill accounting creation"""
    try:
        # Create a test purchase bill
        purchase_bill = PurchaseBill.objects.create(
            bill_number=f"TEST-{date.today().strftime('%Y%m%d')}-001",
            bill_date=date.today(),
            vendor_name="Test Vendor",
            vendor_address="Test Address",
            total_amount=500.00,
            created_by=request.user
        )
        
        print(f"DEBUG: Created purchase bill {purchase_bill.bill_number}")
        
        # Try to create accounting entries manually
        try:
            success = AccountingService.create_purchase_bill_entries(purchase_bill)
            
            return Response({
                'success': True,
                'message': 'Purchase bill and accounting entries created successfully',
                'purchase_bill_id': str(purchase_bill.id),
                'accounting_success': success
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to create accounting entries: {str(e)}',
                'purchase_bill_id': str(purchase_bill.id)
            })
            
    except Exception as e:
        return Response({
            'success': False,
            'error': f'Failed to create purchase bill: {str(e)}'
        })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def test_sales_invoice_accounting(request):
    """Test endpoint to debug sales invoice accounting creation"""
    try:
        from billing.models import SalesInvoice
        
        # Create a test sales invoice
        sales_invoice = SalesInvoice.objects.create(
            invoice_number=f"TEST-{date.today().strftime('%Y%m%d')}-001",
            invoice_date=date.today(),
            customer_name="Test Customer",
            total_amount=1000.00,
            created_by=request.user
        )
        
        print(f"DEBUG: Created sales invoice {sales_invoice.invoice_number}")
        
        # Try to create accounting entries manually
        try:
            success = AccountingService.create_sales_invoice_entries(sales_invoice)
            
            return Response({
                'success': True,
                'message': 'Sales invoice and accounting entries created successfully',
                'sales_invoice_id': str(sales_invoice.id),
                'accounting_success': success
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Failed to create accounting entries: {str(e)}',
                'sales_invoice_id': str(sales_invoice.id)
            })
            
    except Exception as e:
        return Response({
            'success': False,
            'error': f'Failed to create sales invoice: {str(e)}'
        })
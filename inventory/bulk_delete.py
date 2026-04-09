from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions, status
from rest_framework.response import Response
from inventory.models import Product

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_delete_products(request):
    ids = request.data.get('ids', [])
    if not ids or not isinstance(ids, list):
        return Response({'error': 'A list of product IDs is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
    deleted_count, _ = Product.objects.filter(
        id__in=ids,
        created_by=request.user.active_tenant
    ).delete()
    
    return Response({'message': f'Successfully deleted {deleted_count} products.', 'deleted_count': deleted_count}, status=status.HTTP_200_OK)

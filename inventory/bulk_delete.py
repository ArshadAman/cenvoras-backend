from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions, status
from rest_framework.response import Response
from django.db import transaction
from django.db.models.deletion import ProtectedError
from inventory.models import Product

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_delete_products(request):
    ids = request.data.get('ids', [])
    archive_protected = request.data.get('archive_protected', False) or request.data.get('archive', False)
    if not ids or not isinstance(ids, list):
        return Response({'error': 'A list of product IDs is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
    products = list(Product.objects.filter(
        id__in=ids,
        created_by=request.user.active_tenant
    ))

    if not products:
        return Response({'error': 'No matching products found to delete.'}, status=status.HTTP_404_NOT_FOUND)

    deleted_count = 0
    archived_count = 0
    protected_products = []

    for product in products:
        try:
            with transaction.atomic():
                product.delete()
                deleted_count += 1
        except ProtectedError:
            if archive_protected:
                product.is_active = False
                product.save(update_fields=['is_active'])
                archived_count += 1
            else:
                protected_products.append(product.name)

    if deleted_count == 0 and archived_count == 0 and protected_products:
        names_preview = ", ".join(f"'{name}'" for name in protected_products[:3])
        if len(protected_products) > 3:
            names_preview += f" and {len(protected_products) - 3} more"
        return Response({
            'error': f'Cannot delete selected products ({names_preview}) because they are linked to existing transactions (invoices, purchases, or stock movements).',
            'protected': protected_products,
            'deleted_count': 0
        }, status=status.HTTP_400_BAD_REQUEST)

    parts = []
    if deleted_count > 0:
        parts.append(f"deleted {deleted_count} product(s)")
    if archived_count > 0:
        parts.append(f"archived {archived_count} product(s)")
    if protected_products:
        parts.append(f"{len(protected_products)} product(s) could not be deleted because they are linked to financial records")

    message = f"Successfully {', and '.join(parts)}." if (deleted_count or archived_count) else f"{len(protected_products)} product(s) protected."

    return Response({
        'message': message,
        'deleted_count': deleted_count,
        'archived_count': archived_count,
        'protected': protected_products
    }, status=status.HTTP_200_OK)

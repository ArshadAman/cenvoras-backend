import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.contrib.auth import get_user_model
from inventory.models import Warehouse, ProductBatch, Product

User = get_user_model()

print(f"Total Users: {User.objects.count()}")
for u in User.objects.all():
    print(f"User: {u.username} (ID: {u.id})")
    print(f"  - Warehouses: {Warehouse.objects.filter(created_by=u).count()}")
    print(f"  - Products: {Product.objects.filter(created_by=u).count()}")
    print(f"  - Batches: {ProductBatch.objects.filter(product__created_by=u).count()}")

print("-" * 30)
print(f"Total Warehouses: {Warehouse.objects.count()}")
print(f"Total Batches: {ProductBatch.objects.count()}")

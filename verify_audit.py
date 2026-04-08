import os
import django
from django.conf import settings

# Setup Django Environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.contrib.auth import get_user_model
from inventory.models import Product, Warehouse
from audit_log.models import AuditLog
from audit_log.middleware import _thread_locals
from django.test import RequestFactory

User = get_user_model()

def run_test():
    print("🚀 Starting Audit Log Verification...")
    
    # 1. Setup User Context
    user, _ = User.objects.get_or_create(username="audit_tester", email="audit@test.com")
    
    # Mock Request for Middleware
    factory = RequestFactory()
    request = factory.get('/')
    request.user = user
    
    # Manually set thread locals (since we aren't going through real middleware stack here)
    _thread_locals.user = user
    _thread_locals.request = request
    
    print(f"User Context Set: {user.email}")

    # 2. CREATE Action
    print("\n[Test 1] Creating Product...")
    p = Product.objects.create(
        name="Audit Test Product",
        created_by=user,
        price=100,
        sale_price=120
    )
    
    log = AuditLog.objects.filter(model_name='Product', object_id=str(p.id), action='CREATE').first()
    if log:
        print(f"✅ SUCCESS: Logged CREATE Action for {log.object_repr}")
    else:
        print("❌ FAILED: CREATE not logged.")

    # 3. UPDATE Action
    print("\n[Test 2] Updating Product...")
    p.name = "Audit Test Product (Updated)"
    p.save()
    
    log = AuditLog.objects.filter(model_name='Product', object_id=str(p.id), action='UPDATE').first()
    if log:
        print(f"✅ SUCCESS: Logged UPDATE Action. Changes: {log.changes.keys()}")
    else:
        print("❌ FAILED: UPDATE not logged.")

    # 4. DELETE Action
    print("\n[Test 3] Deleting Product...")
    p_id = str(p.id)
    p.delete()
    
    log = AuditLog.objects.filter(model_name='Product', object_id=p_id, action='DELETE').first()
    if log:
        print(f"✅ SUCCESS: Logged DELETE Action.")
    else:
        print("❌ FAILED: DELETE not logged.")
        
    # Cleanup
    if hasattr(_thread_locals, 'user'):
        del _thread_locals.user

if __name__ == "__main__":
    run_test()

import os
import django
from django.conf import settings
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from users.models import User
from users.permissions import IsAdminUser, IsSalesmanOrAbove

# Mock View
class AdminView(APIView):
    permission_classes = [IsAdminUser]
    def get(self, request):
        return Response({'status': 'ok'})

class SalesView(APIView):
    permission_classes = [IsSalesmanOrAbove]
    def get(self, request):
        return Response({'status': 'ok'})

def run_test():
    print("🚀 Starting RBAC Verification...")
    
    factory = APIRequestFactory()
    
    # 1. Create Test Users
    admin_user, _ = User.objects.get_or_create(username='admin_test', email='admin@test.com', defaults={'role': 'admin'})
    sales_user, _ = User.objects.get_or_create(username='sales_test', email='sales@test.com', defaults={'role': 'salesman'})
    
    admin_user.role = 'admin'
    admin_user.save()
    
    sales_user.role = 'salesman'
    sales_user.save()
    
    # 2. Test Admin Access to Admin View
    print("\n[Test 1] Admin accessing Admin View...")
    request = factory.get('/admin-view/')
    force_authenticate(request, user=admin_user)
    view = AdminView.as_view()
    response = view(request)
    if response.status_code == 200:
        print("✅ SUCCESS: Admin allowed.")
    else:
        print(f"❌ FAILED: Admin denied ({response.status_code}).")

    # 3. Test Salesman Access to Admin View
    print("\n[Test 2] Salesman accessing Admin View...")
    request = factory.get('/admin-view/')
    force_authenticate(request, user=sales_user)
    view = AdminView.as_view()
    response = view(request)
    if response.status_code == 403:
        print("✅ SUCCESS: Salesman denied.")
    else:
        print(f"❌ FAILED: Salesman allowed ({response.status_code}).")
        
    # 4. Test Salesman Access to Sales View
    print("\n[Test 3] Salesman accessing Sales View...")
    request = factory.get('/sales-view/')
    force_authenticate(request, user=sales_user)
    view = SalesView.as_view()
    response = view(request)
    if response.status_code == 200:
        print("✅ SUCCESS: Salesman allowed.")
    else:
        print(f"❌ FAILED: Salesman denied ({response.status_code}).")

if __name__ == "__main__":
    run_test()

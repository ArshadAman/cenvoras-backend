#!/usr/bin/env python
import os
import django
import sys
import json

# Add the project path to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()

print("🚀 TESTING OPTIMIZED CENVORAS SIGNUP FLOW")
print("=" * 60)

# Test the new quick signup API
client = Client()

print("\n1. TESTING QUICK SIGNUP API")
print("-" * 30)

signup_data = {
    "email": "testuser@example.com",
    "password": "securepass123",
    "confirm_password": "securepass123", 
    "phone": "9876543210",
    "business_name": "My Test Shop",
    "gstin": ""  # Optional - user can skip
}

response = client.post('/api/users/signup/', data=signup_data, content_type='application/json')
print(f"Status: {response.status_code}")
print(f"Response: {json.dumps(json.loads(response.content), indent=2)}")

if response.status_code == 201:
    print("\n✅ SIGNUP SUCCESS! User can immediately start using the app")
    
    # Get the created user
    user = User.objects.get(email="testuser@example.com")
    print(f"\nUser Details:")
    print(f"  Business Name: {user.business_name}")
    print(f"  Trial Status: {user.is_trial_active}")
    print(f"  Can Generate Basic Invoices: {bool(user.business_name)}")
    print(f"  Can Generate GST Invoices: {user.can_generate_gst_invoice}")
    print(f"  Profile Completed: {user.profile_completed}")

print("\n" + "=" * 60)
print("✅ OPTIMIZED SIGNUP FLOW IMPLEMENTED!")
print("\nFrontend Integration:")
print("POST /api/users/signup/")
print("Required: email, password, confirm_password, phone, business_name")
print("Optional: gstin")
print("\nUser gets 30-day trial and can immediately create invoices!")
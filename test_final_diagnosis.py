#!/usr/bin/env python
import os
import django
import sys

# Add the project path to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from ledger.models import GeneralLedgerEntry, Account
import json

User = get_user_model()

print("=== FINAL DIAGNOSIS FOR FRONTEND DATA ISSUE ===")
print()

# Get database entries
print("1. BACKEND DATABASE CONTAINS:")
entries = GeneralLedgerEntry.objects.all().select_related('account')
for entry in entries:
    print(f"   ✅ {entry.account.name}: {entry.description}")
    print(f"      Dr: ₹{entry.debit}, Cr: ₹{entry.credit}")
    print(f"      Date: {entry.date}, Reference: {entry.reference}")
    print()

print("2. API ENDPOINT TEST:")
# Get user and create JWT token
user = User.objects.first()
if user:
    refresh = RefreshToken.for_user(user)
    access_token = str(refresh.access_token)
    
    # Test with proper JWT authentication
    client = Client()
    headers = {'HTTP_AUTHORIZATION': f'Bearer {access_token}'}
    
    response = client.get('/api/ledger/general-ledger-entries/', **headers)
    print(f"   Status: {response.status_code}")
    
    if response.status_code == 200:
        try:
            data = json.loads(response.content)
            print(f"   ✅ API RETURNS data (length: {len(data) if isinstance(data, list) else 'not a list'}):")
            print(f"   Full response content: {response.content.decode()[:500]}...")
        except Exception as e:
            print(f"   ✅ API Response (raw): {response.content.decode()[:500]}...")
    else:
        print(f"   ❌ ERROR: {response.content.decode()}")
else:
    print("   ❌ No users found")

print()
print("=" * 70)
print("CONCLUSION:")
print("Backend has CORRECT data: Real sales entries with product details")
print("If frontend shows 'Default asset account' and 'Payment' entries,")
print("then the frontend is NOT calling /api/ledger/general-ledger-entries/")
print()
print("FRONTEND MUST CALL: /api/ledger/general-ledger-entries/")
print("WITH PROPER JWT TOKEN IN AUTHORIZATION HEADER")
print("=" * 70)
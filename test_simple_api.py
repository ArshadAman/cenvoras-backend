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
from ledger.models import GeneralLedgerEntry

User = get_user_model()

# Create test client
client = Client()

print("=== Testing Ledger API Endpoints ===")
print()

# Test if the endpoint exists (should get 401 or 403, not 404)
print("1. Testing /api/ledger/general-ledger-entries/ without authentication:")
response = client.get('/api/ledger/general-ledger-entries/')
print(f"   Status Code: {response.status_code}")
print(f"   Response: {response.content[:200].decode('utf-8')}")
print()

# Check current ledger entries
print("2. Direct database query for ledger entries:")
entries = GeneralLedgerEntry.objects.all()
print(f"   Total entries in database: {entries.count()}")
for entry in entries:
    print(f"   - {entry.account.name}: {entry.description} | Dr:{entry.debit} Cr:{entry.credit}")
print()

# Test other endpoints
print("3. Testing /api/ledger/accounts/ without authentication:")
response = client.get('/api/ledger/accounts/')
print(f"   Status Code: {response.status_code}")
print(f"   Response: {response.content[:200].decode('utf-8')}")
print()

# Get a user and test with authentication
try:
    user = User.objects.first()
    if user:
        print(f"4. Testing with user authentication (user: {user.username}):")
        client.force_login(user)
        
        response = client.get('/api/ledger/general-ledger-entries/')
        print(f"   Status Code: {response.status_code}")
        print(f"   Response: {response.content[:500].decode('utf-8')}")
        print()
    else:
        print("4. No users found in database")
except Exception as e:
    print(f"4. Error with authentication: {e}")
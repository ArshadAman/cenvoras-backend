#!/usr/bin/env python3
"""
Test script to check what our ledger API returns
This will help identify why the frontend shows different data
"""

import os
import sys
import django
import json
from django.test import Client

# Add the project directory to Python path
sys.path.append('/Users/arshadaman/Cenvoras/cenvoras')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenvoras.settings')
django.setup()

from users.models import User

def test_ledger_api():
    """Test the ledger API endpoint"""
    print("=== TESTING LEDGER API ENDPOINT ===\n")
    
    # Create a test client
    client = Client()
    
    # Get a user to authenticate with
    user = User.objects.first()
    if not user:
        print("❌ No user found for testing")
        return
    
    print(f"✅ Using user: {user.id}")
    
    # Force login the user (simulate authentication)
    client.force_login(user)
    
    # Test the general ledger entries endpoint
    try:
        response = client.get('/api/ledger/general-ledger-entries/')
        
        print(f"\n📡 API Response:")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Success: {data.get('success')}")
            print(f"📊 Count: {data.get('count')}")
            print(f"📝 Entries: {len(data.get('entries', []))}")
            
            print(f"\n📋 ACTUAL API RESPONSE DATA:")
            for i, entry in enumerate(data.get('entries', [])[:5], 1):  # Show first 5
                print(f"\nEntry {i}:")
                print(f"  Account Name: {entry.get('account_name')}")
                print(f"  Description: {entry.get('description')}")
                print(f"  Debit: {entry.get('debit')}")
                print(f"  Credit: {entry.get('credit')}")
                print(f"  Date: {entry.get('date')}")
                print(f"  Reference: {entry.get('reference')}")
        else:
            print(f"❌ Error {response.status_code}: {response.content.decode()}")
            
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    print(f"\n" + "="*60)
    print("FRONTEND SHOULD CALL: /api/ledger/general-ledger-entries/")
    print("MAKE SURE:")
    print("1. Frontend is calling the correct URL")
    print("2. User is properly authenticated") 
    print("3. Not calling a different endpoint")
    print("="*60)

if __name__ == "__main__":
    test_ledger_api()
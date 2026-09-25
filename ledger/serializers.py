from rest_framework import serializers
from .models import Account, GeneralLedgerEntry


class AccountSerializer(serializers.ModelSerializer):
    """Serializer for Chart of Accounts"""
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    parent_account_name = serializers.CharField(source='parent_account.name', read_only=True)
    
    class Meta:
        model = Account
        fields = [
            'id', 'code', 'name', 'account_type', 'parent_account', 'parent_account_name',
            'description', 'is_active', 'created_by', 'created_at'
        ]
        read_only_fields = ['id', 'parent_account_name', 'created_by', 'created_at']
    
    def create(self, validated_data):
        user = self.context['request'].user
        validated_data['created_by'] = getattr(user, 'active_tenant', user)
        return super().create(validated_data)


class GeneralLedgerEntrySerializer(serializers.ModelSerializer):
    """Serializer for General Ledger entries with full partner and document context"""
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    
    # Partner names
    customer_name = serializers.CharField(source='customer.name', read_only=True, default=None)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True, default=None)
    
    # Related document numbers
    sales_invoice_number = serializers.CharField(source='sales_invoice.invoice_number', read_only=True, default=None)
    purchase_bill_number = serializers.CharField(source='purchase_bill.bill_number', read_only=True, default=None)
    credit_note_number = serializers.CharField(source='credit_note.credit_note_number', read_only=True, default=None)
    debit_note_number = serializers.CharField(source='debit_note.debit_note_number', read_only=True, default=None)
    
    # Computed fields (can be passed via context or annotated)
    running_balance = serializers.DecimalField(max_digits=15, decimal_places=2, required=False, read_only=True)
    running_balance_type = serializers.CharField(required=False, read_only=True)
    
    class Meta:
        model = GeneralLedgerEntry
        fields = [
            'id', 'date', 'account', 'account_name', 'account_code', 'account_type',
            'debit', 'credit', 'description', 'reference',
            'customer', 'customer_name', 'vendor', 'vendor_name', 'payment',
            'sales_invoice', 'sales_invoice_number',
            'purchase_bill', 'purchase_bill_number',
            'credit_note', 'credit_note_number',
            'debit_note', 'debit_note_number',
            'running_balance', 'running_balance_type',
            'created_by', 'created_at'
        ]
        read_only_fields = [
            'id', 'account_name', 'account_code', 'account_type',
            'customer_name', 'vendor_name',
            'sales_invoice_number', 'purchase_bill_number',
            'credit_note_number', 'debit_note_number',
            'running_balance', 'running_balance_type',
            'created_by', 'created_at'
        ]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ref = ret.get('reference') or ''
        import re
        if ref and re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', ref, re.I):
            cleaned = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '', ref, flags=re.I).strip()
            ret['reference'] = cleaned or 'Payment Received'
        return ret


class AccountBalanceSerializer(serializers.Serializer):
    """Serializer for account balance summary"""
    account = AccountSerializer(read_only=True)
    debit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    credit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        fields = ['account', 'debit_total', 'credit_total', 'balance']
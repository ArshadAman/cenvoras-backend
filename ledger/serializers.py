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
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class GeneralLedgerEntrySerializer(serializers.ModelSerializer):
    """Serializer for General Ledger entries"""
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    
    # Related document information
    sales_invoice_number = serializers.CharField(source='sales_invoice.invoice_number', read_only=True)
    purchase_bill_number = serializers.CharField(source='purchase_bill.bill_number', read_only=True)
    
    class Meta:
        model = GeneralLedgerEntry
        fields = [
            'id', 'date', 'account', 'account_name', 'account_code', 'account_type',
            'debit', 'credit', 'description', 'reference',
            'sales_invoice', 'sales_invoice_number', 'purchase_bill', 'purchase_bill_number',
            'created_by', 'created_at'
        ]
        read_only_fields = [
            'id', 'account_name', 'account_code', 'account_type',
            'sales_invoice_number', 'purchase_bill_number', 'created_by', 'created_at'
        ]


class AccountBalanceSerializer(serializers.Serializer):
    """Serializer for account balance summary"""
    account = AccountSerializer(read_only=True)
    debit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    credit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        fields = ['account', 'debit_total', 'credit_total', 'balance']


class GeneralLedgerEntrySerializer(serializers.ModelSerializer):
    """Serializer for individual ledger entries"""
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    
    class Meta:
        model = GeneralLedgerEntry
        fields = [
            'id', 'account', 'account_name', 'account_code', 'account_type',
            'debit', 'credit', 'description', 'created_at'
        ]
        read_only_fields = [
            'id', 'account_name', 'account_code', 'account_type',
            'sales_invoice_number', 'purchase_bill_number', 'created_by', 'created_at'
        ]


class AccountBalanceSerializer(serializers.Serializer):
    """Serializer for account balance summary"""
    account = AccountSerializer(read_only=True)
    debit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    credit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        fields = ['account', 'debit_total', 'credit_total', 'balance']
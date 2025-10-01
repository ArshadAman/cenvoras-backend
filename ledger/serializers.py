from rest_framework import serializers
from .models import ClientLedgerEntry, Account, JournalEntry, GeneralLedgerEntry
from billing.models import Customer
from billing.serializers import CustomerSerializer

class ClientLedgerEntrySerializer(serializers.ModelSerializer):
    # For reading (GET) - return full customer object
    customer = CustomerSerializer(read_only=True)
    
    class Meta:
        model = ClientLedgerEntry
        fields = [
            'id', 'customer', 'date', 'description', 'invoice',
            'debit', 'credit', 'balance', 'created_by', 'created_at'
        ]
        read_only_fields = ['balance', 'created_by', 'created_at']
    
    def to_internal_value(self, data):
        """Handle customer UUID in write operations"""
        # Make a copy to avoid modifying the original data
        internal_data = data.copy()
        
        # If customer UUID is provided, validate and convert it
        customer_uuid = internal_data.get('customer')
        if customer_uuid and isinstance(customer_uuid, str):
            request = self.context.get('request')
            if request and request.user:
                try:
                    customer = Customer.objects.get(id=customer_uuid, created_by=request.user)
                    # Replace UUID string with customer object for internal processing
                    internal_data['customer'] = customer
                except Customer.DoesNotExist:
                    raise serializers.ValidationError({
                        'customer': 'Customer not found or doesn\'t belong to you.'
                    })
                except ValueError:
                    raise serializers.ValidationError({
                        'customer': 'Invalid customer ID format.'
                    })
        
        return super().to_internal_value(internal_data)


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
        read_only_fields = ['id', 'created_by', 'created_at', 'parent_account_name']
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


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
        read_only_fields = ['id', 'account_name', 'account_code', 'account_type', 'created_at']


class JournalEntrySerializer(serializers.ModelSerializer):
    """Serializer for journal entries with nested ledger entries"""
    ledger_entries = GeneralLedgerEntrySerializer(many=True, read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    total_debits = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_credits = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_balanced = serializers.BooleanField(read_only=True)
    
    # Related document information
    sales_invoice_number = serializers.CharField(source='sales_invoice.invoice_number', read_only=True)
    purchase_bill_number = serializers.CharField(source='purchase_bill.bill_number', read_only=True)
    
    class Meta:
        model = JournalEntry
        fields = [
            'id', 'date', 'description', 'reference', 'sales_invoice', 'sales_invoice_number',
            'purchase_bill', 'purchase_bill_number', 'ledger_entries', 'total_debits', 
            'total_credits', 'is_balanced', 'created_by', 'created_at'
        ]
        read_only_fields = [
            'id', 'ledger_entries', 'total_debits', 'total_credits', 'is_balanced',
            'sales_invoice_number', 'purchase_bill_number', 'created_by', 'created_at'
        ]
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class AccountBalanceSerializer(serializers.Serializer):
    """Serializer for account balance summary"""
    account = AccountSerializer(read_only=True)
    debit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    credit_total = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        fields = ['account', 'debit_total', 'credit_total', 'balance']
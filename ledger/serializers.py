from rest_framework import serializers
from .models import ClientLedgerEntry

class ClientLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientLedgerEntry
        fields = [
            'id', 'customer', 'date', 'description', 'invoice',
            'debit', 'credit', 'balance', 'created_by', 'created_at'
        ]
        read_only_fields = ['balance', 'created_by', 'created_at']
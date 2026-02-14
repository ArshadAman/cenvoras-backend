from rest_framework import generics, permissions
from .models import AuditLog
from .serializers import AuditLogSerializer

class AuditLogListView(generics.ListAPIView):
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated] # Later restrict to Admin/Manager
    filterset_fields = ['action', 'model_name', 'user__email']
    search_fields = ['object_repr', 'changes', 'user_email']
    ordering_fields = ['timestamp']

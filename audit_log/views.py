from rest_framework import generics, permissions
from .models import AuditLog
from .serializers import AuditLogSerializer

class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    
    def get_queryset(self):
        # Exclude internal system actions (e.g. migrations, background tasks)
        return AuditLog.objects.exclude(user_email='system').order_by('-timestamp')
    permission_classes = [permissions.IsAuthenticated] # Later restrict to Admin/Manager
    filterset_fields = ['action', 'model_name', 'user__email']
    search_fields = ['object_repr', 'changes', 'user_email']
    ordering_fields = ['timestamp']

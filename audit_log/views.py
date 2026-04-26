from rest_framework import generics, permissions
from .models import AuditLog
from .serializers import AuditLogSerializer

class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        # System admins (superusers) see everything
        if user.is_superuser:
            return AuditLog.objects.exclude(user_email='system').order_by('-timestamp')
            
        # Tenant admins and team members see logs for their shared business (active_tenant)
        tenant = getattr(user, 'active_tenant', user)
        return AuditLog.objects.filter(tenant=tenant).order_by('-timestamp')

    filterset_fields = ['action', 'model_name', 'user__email', 'tenant__id']
    search_fields = ['object_repr', 'changes', 'user_email']
    ordering_fields = ['timestamp']

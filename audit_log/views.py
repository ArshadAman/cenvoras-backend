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
        # We explicitly exclude actions performed by superusers or system to avoid security leaks
        tenant = getattr(user, 'active_tenant', user)
        
        qs = AuditLog.objects.filter(tenant=tenant).exclude(user_email='system')
        
        # Security: Hide any action performed by a superuser
        qs = qs.exclude(user__is_superuser=True)
        
        # Safety net: Hide actions by common admin emails if they aren't meant for the tenant
        # (Though tenant filtering should handle this, we add it as a secondary layer)
        qs = qs.exclude(user_email__in=['cenvoras@gmail.com'])
        
        return qs.order_by('-timestamp')

    filterset_fields = ['action', 'model_name', 'user__email', 'tenant__id']
    search_fields = ['object_repr', 'changes', 'user_email']
    ordering_fields = ['timestamp']

from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from django.views.generic import RedirectView
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
   openapi.Info(
      title="Cenvoras API",
      default_version='v1',
      description="API documentation for your ERP",
   ),
   public=True,
   permission_classes=(permissions.AllowAny,),
)

def favicon_view(request):
    """Simple favicon response to avoid 404 errors"""
    return HttpResponse(status=204)  # No Content

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/users/', include('users.urls')),
    path('api/billing/', include('billing.urls')),
    path('api/ledger/', include('ledger.urls')),
    path('api/inventory/', include('inventory.urls')),
    path('api/analytics/', include('analytics.urls')),
    path('api/integration/', include('integration.urls')),
    path('api/reports/', include('reports.urls')),
    path('api/audit/', include('audit_log.urls')),
    path('api/ai/', include('ai_assistant.urls')),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    
    # Handle favicon requests
    path('favicon.ico', favicon_view, name='favicon'),
    
    # Redirect root to swagger for development
    path('', RedirectView.as_view(url='/swagger/', permanent=False), name='root'),
]

# Serve static and media files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


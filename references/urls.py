from django.urls import path
from . import views

app_name = 'references'

urlpatterns = [
    # SEO pages (public, no auth required)
    path('hsn/<slug:slug>/', views.hsn_code_detail, name='hsn-detail'),
    path('gst-rate/<slug:slug>/', views.gst_rate_detail, name='gst-detail'),
    
    # API endpoints under /api/references/
    path('api/references/hsn-search/', views.hsn_search, name='api-hsn-search'),
    path('api/references/gst-rate/', views.gst_rate_by_category, name='api-gst-rate'),
    path('api/references/hsn-gst/<str:hsn_code>/', views.hsn_gst_combined, name='api-hsn-gst'),
    path('api/references/stats/', views.reference_stats, name='api-stats'),

    # Direct API aliases for backward compatibility
    path('api/hsn-search/', views.hsn_search),
    path('api/gst-rate/', views.gst_rate_by_category),
    path('api/hsn-gst/<str:hsn_code>/', views.hsn_gst_combined),
    path('api/stats/', views.reference_stats),
]

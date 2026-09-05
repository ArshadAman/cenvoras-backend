from django.urls import path
from . import views

app_name = 'references'

urlpatterns = [
    # SEO pages (public, no auth required)
    path('hsn/<slug:slug>/', views.hsn_code_detail, name='hsn-detail'),
    path('gst-rate/<slug:slug>/', views.gst_rate_detail, name='gst-detail'),
    
    # API endpoints
    path('api/hsn-search/', views.hsn_search, name='api-hsn-search'),
    path('api/gst-rate/', views.gst_rate_by_category, name='api-gst-rate'),
    path('api/hsn-gst/<str:hsn_code>/', views.hsn_gst_combined, name='api-hsn-gst'),
    path('api/stats/', views.reference_stats, name='api-stats'),
]

from django.urls import path
from .views import plan_catalog, subscription_entitlements

urlpatterns = [
    path('plans/', plan_catalog, name='plan_catalog'),
    path('entitlements/', subscription_entitlements, name='subscription_entitlements'),
]

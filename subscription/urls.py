from django.urls import path
from .views import (
    plan_catalog,
    subscription_entitlements,
    create_plan_payment_order,
    confirm_plan_payment,
)

urlpatterns = [
    path('plans/', plan_catalog, name='plan_catalog'),
    path('entitlements/', subscription_entitlements, name='subscription_entitlements'),
    path('payments/create-order/', create_plan_payment_order, name='create_plan_payment_order'),
    path('payments/confirm/', confirm_plan_payment, name='confirm_plan_payment'),
]

from django.urls import path

from .views import (
    confirm_plan_payment,
    create_plan_payment_order,
    latest_payment_status,
    plan_catalog,
    plan_change_quote,
    schedule_plan_change,
    subscription_entitlements,
)

urlpatterns = [
    path('plans/', plan_catalog, name='plan_catalog'),
    path('entitlements/', subscription_entitlements, name='subscription_entitlements'),
    path('plan-change/quote/', plan_change_quote, name='plan_change_quote'),
    path('plan-change/schedule/', schedule_plan_change, name='schedule_plan_change'),
    path('payments/create-order/', create_plan_payment_order, name='create_plan_payment_order'),
    path('payments/confirm/', confirm_plan_payment, name='confirm_plan_payment'),
    path('payments/latest-status/', latest_payment_status, name='latest_payment_status'),
]

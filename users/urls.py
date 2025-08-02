from django.urls import path
from .views import (
    register_view, CustomTokenObtainPairView, profile_view,
    password_reset_request_view, password_reset_confirm_view
)

urlpatterns = [
    path('register/', register_view, name='register'),
    path('login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('profile/', profile_view, name='profile'),
    path('password-reset/', password_reset_request_view, name='password_reset_request'),
    path('password-reset-confirm/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
]
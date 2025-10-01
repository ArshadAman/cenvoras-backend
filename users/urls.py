from django.urls import path
from .views import (
    quick_signup_view, CustomTokenObtainPairView, profile_view, profile_setup_view,
    password_reset_request_view, password_reset_confirm_view
)

urlpatterns = [
    # 🚀 Optimized Signup Flow
    path('signup/', quick_signup_view, name='quick_signup'),  # Step 1: Minimal friction signup
    path('login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('profile/', profile_view, name='profile'),  # Get complete profile
    path('profile/setup/', profile_setup_view, name='profile_setup'),  # Step 2: Complete profile inside app
    
    # Password Management
    path('password-reset/', password_reset_request_view, name='password_reset_request'),
    path('password-reset-confirm/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
    
    # Backward compatibility (optional - can remove later)
    path('register/', quick_signup_view, name='register_legacy'),
]
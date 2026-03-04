from django.urls import path
from .views import (
    quick_signup_view, CustomTokenObtainPairView, view_profile, update_profile,
    profile_setup_view, password_reset_request_view, password_reset_confirm_view
)
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework.routers import DefaultRouter
from .views import TeamViewSet

router = DefaultRouter()
router.register(r'team', TeamViewSet, basename='team')

urlpatterns = [
    # 🚀 Optimized Signup Flow
    path('signup/', quick_signup_view, name='quick_signup'),  # Step 1: Minimal friction signup
    path('login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # 👤 Profile Management
    path('profile/', view_profile, name='view_profile'),  # GET: View complete profile
    path('profile/update/', update_profile, name='update_profile'),  # PUT/PATCH: Update profile
    path('profile/setup/', profile_setup_view, name='profile_setup'),  # Step 2: Complete profile inside app
    
    # Password Management
    path('password-reset/', password_reset_request_view, name='password_reset_request'),
    path('password-reset-confirm/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
    
    # Backward compatibility (optional - can remove later)
    # Backward compatibility (optional - can remove later)
    path('register/', quick_signup_view, name='register_legacy'),
] + router.urls
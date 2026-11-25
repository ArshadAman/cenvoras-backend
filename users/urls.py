from django.urls import path
from .views import (
    quick_signup_view, CustomTokenObtainPairView, google_login_view, view_profile, update_profile,
    profile_setup_view, password_reset_request_view, password_reset_confirm_view,
    password_reset_otp_confirm_view, change_password_view
)
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework.routers import DefaultRouter
from .views import TeamViewSet
from . import migration_views

router = DefaultRouter()
router.register(r'team', TeamViewSet, basename='team')

urlpatterns = [
    # 🚀 Optimized Signup Flow
    path('signup/', quick_signup_view, name='quick_signup'),  # Step 1: Minimal friction signup
    path('login/', CustomTokenObtainPairView.as_view(), name='login'),
    path('google-login/', google_login_view, name='google_login'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # 👤 Profile Management
    path('profile/', view_profile, name='view_profile'),  # GET: View complete profile
    path('profile/update/', update_profile, name='update_profile'),  # PUT/PATCH: Update profile
    path('profile/setup/', profile_setup_view, name='profile_setup'),  # Step 2: Complete profile inside app
    path('change-password/', change_password_view, name='change_password'),
    
    # Password Management
    path('password-reset/', password_reset_request_view, name='password_reset_request'),
    path('password-reset-otp-confirm/', password_reset_otp_confirm_view, name='password_reset_otp_confirm'),
    path('password-reset-confirm/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
    
    # Backward compatibility (optional - can remove later)
    # Backward compatibility (optional - can remove later)
    path('register/', quick_signup_view, name='register_legacy'),

    # Marg ERP & Tally Migration Pipeline
    path('migration/marg-preview/', migration_views.marg_preview_view, name='marg_preview_view'),
    path('migration/marg-dry-run/', migration_views.marg_dry_run_view, name='marg_dry_run_view'),
    path('migration/marg-commit/', migration_views.marg_commit_view, name='marg_commit_view'),
    path('migration/marg-dbf-dry-run/', migration_views.marg_dbf_dry_run_view, name='marg_dbf_dry_run_view'),
    path('migration/marg-dbf-commit/', migration_views.marg_dbf_commit_view, name='marg_dbf_commit_view'),
    path('migration/tally-dry-run/', migration_views.tally_dry_run_view, name='tally_dry_run_view'),
    path('migration/tally-commit/', migration_views.tally_commit_view, name='tally_commit_view'),
] + router.urls
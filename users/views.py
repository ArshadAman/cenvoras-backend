from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import AnonRateThrottle
from django.db import IntegrityError, transaction
from .serializers import (
    QuickSignupSerializer, ProfileSetupSerializer, UserProfileSerializer, 
    ProfileUpdateSerializer, CustomTokenObtainPairSerializer
)
from rest_framework_simplejwt.views import TokenObtainPairView
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.conf import settings
from .tasks import send_async_email
from django.core.cache import cache
from django.contrib.auth.hashers import make_password
import secrets
import hashlib
import hmac
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from subscription.services import get_effective_limit, get_limit_exceeded_reason

class RegisterRateThrottle(AnonRateThrottle):
    rate = '5/min'  # Limit registration attempts


class OtpRateThrottle(AnonRateThrottle):
    rate = '10/min'


SIGNUP_OTP_TTL_SECONDS = 10 * 60
FORGOT_OTP_TTL_SECONDS = 10 * 60


def _normalize_email(value: str) -> str:
    return str(value or '').strip().lower()


def _otp_hash(email: str, purpose: str, otp: str) -> str:
    base = f"{purpose}:{_normalize_email(email)}:{otp}:{settings.SECRET_KEY}"
    return hashlib.sha256(base.encode('utf-8')).hexdigest()


def _generate_otp() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def _signup_pending_key(email: str) -> str:
    return f"auth:signup:pending:{_normalize_email(email)}"


def _signup_otp_key(email: str) -> str:
    return f"auth:signup:otp:{_normalize_email(email)}"


def _forgot_otp_key(email: str) -> str:
    return f"auth:forgot:otp:{_normalize_email(email)}"


def _send_otp_email(email: str, otp: str, subject: str, intro: str) -> None:
    body = (
        f"Hello,\n\n"
        f"{intro}\n\n"
        f"Your OTP is: {otp}\n"
        f"This OTP is valid for 10 minutes.\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Regards,\n"
        f"Cenvora Team"
    )
    send_async_email.delay(
        subject=subject,
        message=body,
        recipient_list=[email],
        force_cenvora_branding=True,
    )

# Create your views here.

@swagger_auto_schema(
    method='post',
    request_body=QuickSignupSerializer,
    responses={201: openapi.Response(
        description="User registered successfully - minimal friction signup",
        examples={
            "application/json": {
                "success": True,
                "message": "Account created successfully! Welcome to Cenvoras.",
                "user": {
                    "id": "uuid",
                    "email": "user@example.com",
                    "business_name": "My Shop",
                    "trial_ends_at": "2025-11-01T10:00:00Z",
                    "profile_completed": False
                },
                "next_step": "Complete your profile to start creating GST-compliant invoices"
            }
        }
    ), 400: openapi.Response(
        description="Validation error",
        examples={
            "application/json": {
                "success": False,
                "errors": {
                    "email": ["Email already registered"]
                }
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def quick_signup_view(request):
    """
    🚀 CENVORAS QUICK SIGNUP - Minimal Friction Onboarding
    
    Only asks for essential fields:
    - Email (login)
    - Password (security)
    - Phone (recovery + invoices)
    - Business Name (appears on invoices)
    - GST Number (optional - for GST-compliant invoices)
    
    Everything else can be completed later in profile setup!
    """
    otp = str(request.data.get('otp', '')).strip()
    email = _normalize_email(request.data.get('email'))

    # Step 1: request OTP
    if not otp:
        serializer = QuickSignupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        normalized_email = _normalize_email(validated['email'])
        generated_otp = _generate_otp()

        pending_payload = {
            'email': normalized_email,
            'username': normalized_email,
            'phone': validated['phone'],
            'business_name': validated['business_name'],
            'gstin': validated.get('gstin', ''),
            'password_hash': make_password(validated['password']),
            'requested_at': timezone.now().isoformat(),
        }

        cache.set(_signup_pending_key(normalized_email), pending_payload, timeout=SIGNUP_OTP_TTL_SECONDS)
        cache.set(
            _signup_otp_key(normalized_email),
            {
                'hash': _otp_hash(normalized_email, 'signup', generated_otp),
                'attempts': 0,
            },
            timeout=SIGNUP_OTP_TTL_SECONDS,
        )

        _send_otp_email(
            normalized_email,
            generated_otp,
            subject='Verify your Cenvora signup OTP',
            intro='Use the OTP below to complete your Cenvora account signup.',
        )

        return Response({
            'success': True,
            'otp_required': True,
            'message': 'OTP sent to your email. Please verify to complete signup.',
            'expires_in_seconds': SIGNUP_OTP_TTL_SECONDS,
        }, status=status.HTTP_200_OK)

    # Step 2: verify OTP + create account
    if not email:
        return Response({'success': False, 'error': 'Email is required with OTP verification.'}, status=status.HTTP_400_BAD_REQUEST)

    otp_record = cache.get(_signup_otp_key(email)) or {}
    pending = cache.get(_signup_pending_key(email))
    if not otp_record or not pending:
        return Response({'success': False, 'error': 'OTP expired or invalid. Please request a new OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    attempts = int(otp_record.get('attempts', 0))
    if attempts >= 5:
        cache.delete(_signup_otp_key(email))
        cache.delete(_signup_pending_key(email))
        return Response({'success': False, 'error': 'Too many invalid attempts. Please request a new OTP.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    expected_hash = str(otp_record.get('hash', ''))
    provided_hash = _otp_hash(email, 'signup', otp)
    if not expected_hash or not hmac.compare_digest(expected_hash, provided_hash):
        otp_record['attempts'] = attempts + 1
        cache.set(_signup_otp_key(email), otp_record, timeout=SIGNUP_OTP_TTL_SECONDS)
        return Response({'success': False, 'error': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            User = get_user_model()
            if User.objects.filter(email=email).exists() or User.objects.filter(phone=pending.get('phone')).exists():
                return Response({
                    'success': False,
                    'error': 'A user with this email or phone already exists.'
                }, status=status.HTTP_400_BAD_REQUEST)

            trial_end = timezone.now() + timedelta(days=14)
            user = User.objects.create(
                username=pending['username'],
                email=pending['email'],
                phone=pending['phone'],
                business_name=pending['business_name'],
                gstin=pending.get('gstin', ''),
                trial_ends_at=trial_end,
                password=pending['password_hash'],
                last_login_at=timezone.now(),
            )

            cache.delete(_signup_otp_key(email))
            cache.delete(_signup_pending_key(email))

            user_data = UserProfileSerializer(user).data
            return Response({
                'success': True,
                'message': 'Account created successfully! Welcome to Cenvoras.',
                'user': user_data,
                'next_step': 'Complete your profile to start creating GST-compliant invoices',
                'trial_info': {
                    'trial_active': user.is_trial_active,
                    'trial_ends_at': user.trial_ends_at,
                    'days_remaining': (user.trial_ends_at - timezone.now()).days if user.trial_ends_at else 14
                }
            }, status=status.HTTP_201_CREATED)
    except IntegrityError:
        return Response({
            'success': False,
            'error': 'A user with this email or phone already exists.'
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        return Response({
            'success': False,
            'error': 'Registration failed. Please try again later.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="Complete user profile with business details and subscription info",
        examples={
            "application/json": {
                "success": True,
                "profile": {
                    "id": "uuid",
                    "email": "user@example.com",
                    "business_name": "My Shop",
                    "phone": "9876543210",
                    "first_name": "John",
                    "last_name": "Doe",
                    "gstin": "29ABCDE1234F1Z5",
                    "business_address": "123 Main St, City",
                    "subscription_status": "trial",
                    "trial_ends_at": "2025-11-01T10:00:00Z",
                    "profile_completed": True,
                    "can_generate_gst_invoice": True,
                    "is_trial_active": True
                },
                "setup_progress": {
                    "signup_completed": True,
                    "profile_completed": True,
                    "can_create_invoices": True,
                    "can_create_gst_invoices": True,
                    "completion_percentage": 100,
                    "next_steps": []
                },
                "account_stats": {
                    "days_since_signup": 5,
                    "trial_days_remaining": 25,
                    "total_invoices": 0,
                    "total_customers": 0
                }
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def view_profile(request):
    """
    👤 VIEW PROFILE - Get complete user profile with business and subscription details
    
    Returns comprehensive profile information including:
    - Personal & business details
    - Subscription status & trial info
    - Profile completion status
    - Account capabilities
    - Usage statistics
    """
    user = request.user
    user.last_login_at = timezone.now()
    user.save(update_fields=['last_login_at'])
    
    # Calculate profile completion percentage
    total_fields = 7  # email, phone, business_name, first_name, last_name, business_address, gstin
    completed_fields = 0
    
    if user.email: completed_fields += 1
    if user.phone: completed_fields += 1
    if user.business_name: completed_fields += 1
    if user.first_name: completed_fields += 1
    if user.last_name: completed_fields += 1
    if user.business_address: completed_fields += 1
    if user.gstin: completed_fields += 1
    
    completion_percentage = int((completed_fields / total_fields) * 100)
    
    # Calculate account stats
    days_since_signup = (timezone.now() - user.date_joined).days
    trial_days_remaining = 0
    if user.trial_ends_at and user.is_trial_active:
        trial_days_remaining = (user.trial_ends_at - timezone.now()).days
    
    # Get usage stats (you can expand this)
    from billing.models import SalesInvoice, Customer
    total_invoices = SalesInvoice.objects.filter(created_by=user).count()
    total_customers = Customer.objects.filter(created_by=user).count()
    
    serializer = UserProfileSerializer(user)
    return Response({
        'success': True,
        'profile': serializer.data,
        'setup_progress': {
            'signup_completed': True,
            'profile_completed': user.profile_completed,
            'can_create_invoices': bool(user.business_name),
            'can_create_gst_invoices': user.can_generate_gst_invoice,
            'completion_percentage': completion_percentage,
            'next_steps': _get_profile_next_steps(user)
        },
        'account_stats': {
            'days_since_signup': days_since_signup,
            'trial_days_remaining': max(0, trial_days_remaining),
            'total_invoices': total_invoices,
            'total_customers': total_customers
        }
    })

@swagger_auto_schema(
    method='put',
    request_body=ProfileUpdateSerializer,
    responses={200: openapi.Response(
        description="Profile updated successfully",
        examples={
            "application/json": {
                "success": True,
                "message": "Profile updated successfully",
                "profile": {
                    "id": "uuid",
                    "email": "updated@example.com",
                    "business_name": "Updated Shop Name",
                    "phone": "9876543210",
                    "first_name": "John",
                    "last_name": "Doe",
                    "gstin": "29ABCDE1234F1Z5",
                    "business_address": "123 Updated St, City"
                },
                "changes_made": ["business_name", "first_name"],
                "capabilities": {
                    "can_create_basic_invoices": True,
                    "can_create_gst_invoices": True,
                    "profile_completed": True
                }
            }
        }
    ), 400: openapi.Response(
        description="Validation error",
        examples={
            "application/json": {
                "success": False,
                "errors": {
                    "email": ["A user with this email already exists"],
                    "current_password": ["Current password is incorrect"]
                }
            }
        }
    )}
)
@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def update_profile(request):
    """
    ✏️ UPDATE PROFILE - Comprehensive profile update with validation
    
    Allows updating:
    - Personal info (first_name, last_name)
    - Contact info (phone, email - requires current password)
    - Business details (business_name, business_address, gstin)
    - Password (requires current password + confirmation)
    
    Security features:
    - Current password required for email/password changes
    - Email uniqueness validation
    - Phone uniqueness validation
    - Password confirmation matching
    """
    user = request.user
    
    # Track what fields are being changed
    original_data = {
        'email': user.email,
        'phone': user.phone,
        'business_name': user.business_name,
        'invoice_prefix': user.invoice_prefix,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'business_address': user.business_address,
        'gstin': user.gstin,
        'gem_id': user.gem_id,
        'dl_number': user.dl_number,
    }
    
    # Treat PUT as partial as well, since frontend sends only editable fields.
    serializer = ProfileUpdateSerializer(user, data=request.data, partial=(request.method in ['PATCH', 'PUT']))
    
    if serializer.is_valid():
        updated_user = serializer.save()
        
        # Determine what changed
        changes_made = []
        new_data = {
            'email': updated_user.email,
            'phone': updated_user.phone,
            'business_name': updated_user.business_name,
            'invoice_prefix': updated_user.invoice_prefix,
            'first_name': updated_user.first_name,
            'last_name': updated_user.last_name,
            'business_address': updated_user.business_address,
            'gstin': updated_user.gstin,
            'gem_id': updated_user.gem_id,
            'dl_number': updated_user.dl_number,
        }
        
        for field, old_value in original_data.items():
            if old_value != new_data[field]:
                changes_made.append(field)
        
        # Check for password change
        if 'new_password' in request.data:
            changes_made.append('password')
        
        # Determine new capabilities
        can_create_basic_invoices = bool(updated_user.business_name)
        can_create_gst_invoices = updated_user.can_generate_gst_invoice
        
        # Generate success message
        if changes_made:
            message = f"Profile updated successfully. Changed: {', '.join(changes_made)}"
        else:
            message = "No changes were made to your profile"
        
        return Response({
            'success': True,
            'message': message,
            'profile': UserProfileSerializer(updated_user).data,
            'changes_made': changes_made,
            'capabilities': {
                'can_create_basic_invoices': can_create_basic_invoices,
                'can_create_gst_invoices': can_create_gst_invoices,
                'profile_completed': updated_user.profile_completed
            }
        })
    
    return Response({
        'success': False,
        'message': 'Profile update validation failed',
        'errors': serializer.errors
    }, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='put',
    request_body=ProfileSetupSerializer,
    responses={200: openapi.Response(
        description="Profile setup completed",
        examples={
            "application/json": {
                "success": True,
                "message": "Profile updated successfully",
                "profile_completed": True,
                "can_generate_gst_invoice": True
            }
        }
    )}
)
@api_view(['PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def profile_setup_view(request):
    """
    📋 PROFILE SETUP - Complete business details inside the app
    
    This is Step 2 after quick signup. Users can complete:
    - Full name (first_name, last_name)
    - Complete business address (required for invoices)
    - GST number (if not provided during signup)
    - Phone update if needed
    """
    user = request.user
    serializer = ProfileSetupSerializer(user, data=request.data, partial=(request.method == 'PATCH'))
    
    if serializer.is_valid():
        serializer.save()
        
        # Check what the user can now do
        can_create_basic_invoices = bool(user.business_name)
        can_create_gst_invoices = user.can_generate_gst_invoice
        
        next_steps = []
        if not user.business_address:
            next_steps.append("Add business address to generate invoices")
        if not user.gstin:
            next_steps.append("Add GST number for GST-compliant invoices")
        if not next_steps:
            next_steps.append("Your profile is complete! Start creating invoices.")
        
        return Response({
            'success': True,
            'message': 'Profile updated successfully',
            'profile': UserProfileSerializer(user).data,
            'capabilities': {
                'can_create_basic_invoices': can_create_basic_invoices,
                'can_create_gst_invoices': can_create_gst_invoices,
                'profile_completed': user.profile_completed
            },
            'next_steps': next_steps
        })
    
    return Response({
        'success': False,
        'errors': serializer.errors
    }, status=status.HTTP_400_BAD_REQUEST)

def _get_profile_next_steps(user):
    """Helper function to determine what user should do next"""
    steps = []
    
    if not user.business_name:
        steps.append("Add business/shop name")
    if not user.business_address:
        steps.append("Add business address for invoice generation")
    if not user.gstin:
        steps.append("Add GST number for tax-compliant invoices (optional)")
    if not (user.first_name and user.last_name):
        steps.append("Complete your full name")
    
    if not steps:
        steps.append("Profile complete! Start managing your business.")
    
    return steps

@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['email'],
        properties={
            'email': openapi.Schema(type=openapi.TYPE_STRING, description='User email')
        }
    ),
    responses={200: openapi.Response(
        description="Password reset link sent",
        examples={
            "application/json": {
                "message": "Password reset link sent."
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([OtpRateThrottle])
def password_reset_request_view(request):
    email = _normalize_email(request.data.get('email'))
    if not email:
        return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)
    User = get_user_model()

    otp = _generate_otp()
    cache.set(
        _forgot_otp_key(email),
        {
            'hash': _otp_hash(email, 'forgot', otp),
            'attempts': 0,
        },
        timeout=FORGOT_OTP_TTL_SECONDS,
    )

    try:
        user = User.objects.get(email=email)
        _send_otp_email(
            email,
            otp,
            subject='Cenvora password reset OTP',
            intro='Use the OTP below to reset your Cenvora account password.',
        )
        
        return Response({'message': 'Password reset OTP sent.'})
    except User.DoesNotExist:
        # Do not reveal if email exists for security
        return Response({'message': 'Password reset OTP sent.'})


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([OtpRateThrottle])
def password_reset_otp_confirm_view(request):
    email = _normalize_email(request.data.get('email'))
    otp = str(request.data.get('otp', '')).strip()
    password = request.data.get('password')

    if not email or not otp or not password:
        return Response({'error': 'email, otp and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    otp_record = cache.get(_forgot_otp_key(email)) or {}
    if not otp_record:
        return Response({'error': 'OTP expired or invalid. Please request a new OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    attempts = int(otp_record.get('attempts', 0))
    if attempts >= 5:
        cache.delete(_forgot_otp_key(email))
        return Response({'error': 'Too many invalid attempts. Please request a new OTP.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    expected_hash = str(otp_record.get('hash', ''))
    provided_hash = _otp_hash(email, 'forgot', otp)
    if not expected_hash or not hmac.compare_digest(expected_hash, provided_hash):
        otp_record['attempts'] = attempts + 1
        cache.set(_forgot_otp_key(email), otp_record, timeout=FORGOT_OTP_TTL_SECONDS)
        return Response({'error': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)

    User = get_user_model()
    try:
        user = User.objects.get(email=email)
        user.set_password(password)
        user.save(update_fields=['password'])
        cache.delete(_forgot_otp_key(email))
        return Response({'message': 'Password has been reset.'})
    except User.DoesNotExist:
        return Response({'error': 'Invalid request.'}, status=status.HTTP_400_BAD_REQUEST)

@swagger_auto_schema(
    method='post',
    request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['password'],
        properties={
            'password': openapi.Schema(type=openapi.TYPE_STRING, description='New password')
        }
    ),
    responses={200: openapi.Response(
        description="Password has been reset",
        examples={
            "application/json": {
                "message": "Password has been reset."
            }
        }
    ), 400: openapi.Response(
        description="Invalid or expired token",
        examples={
            "application/json": {
                "error": "Invalid or expired token."
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm_view(request, uidb64, token):
    password = request.data.get('password')
    if not password:
        return Response({'error': 'Password is required.'}, status=status.HTTP_400_BAD_REQUEST)
    User = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
        if default_token_generator.check_token(user, token):
            user.set_password(password)
            user.save()
            return Response({'message': 'Password has been reset.'})
        else:
            return Response({'error': 'Invalid or expired token.'}, status=status.HTTP_400_BAD_REQUEST)
    except (User.DoesNotExist, ValueError, TypeError):
        return Response({'error': 'Invalid request.'}, status=status.HTTP_400_BAD_REQUEST)

from rest_framework import viewsets
from .serializers import TeamMemberSerializer

class TeamViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Admins to view, create, and modify their Team Managers.
    Enforces subscription limits: Free=0, Mid=2, Pro=5.
    """
    serializer_class = TeamMemberSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        User = get_user_model()
        if getattr(self, 'swagger_fake_view', False): return User.objects.none()
        
        user = self.request.user
        tenant = user.active_tenant
        
        # Users can only see team members belonging to their tenant
        # Managers shouldn't be able to edit other managers, but Admins can.
        if user.role != 'admin':
            return User.objects.none() # Or filter(id=user.id) if they should see themselves
            
        return User.objects.filter(parent=tenant).exclude(id=tenant.id).order_by('-date_joined')
        
    def perform_create(self, serializer):
        User = get_user_model()
        tenant = self.request.user.active_tenant

        max_members = get_effective_limit(tenant, 'max_team_members', 0)
        current_members = get_user_model().objects.filter(parent=tenant).exclude(id=tenant.id).count()
        reason = get_limit_exceeded_reason(tenant, 'team')
        if reason:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(reason)
        if current_members >= max_members:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(f"Your current plan limits you to {max_members} team members. Upgrade for more.")
            
        serializer.save()



from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import AnonRateThrottle
from django.db import IntegrityError, transaction
from .serializers import (
    QuickSignupSerializer, ProfileSetupSerializer, UserProfileSerializer, 
    CustomTokenObtainPairSerializer
)
from rest_framework_simplejwt.views import TokenObtainPairView
from django.utils import timezone
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

class RegisterRateThrottle(AnonRateThrottle):
    rate = '5/min'  # Limit registration attempts

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
    serializer = QuickSignupSerializer(data=request.data)
    if serializer.is_valid():
        try:
            with transaction.atomic():
                user = serializer.save()
                user.last_login_at = timezone.now()
                user.save(update_fields=['last_login_at'])
                
                # Return user info for immediate use
                user_data = UserProfileSerializer(user).data
                
                return Response({
                    'success': True,
                    'message': 'Account created successfully! Welcome to Cenvoras.',
                    'user': user_data,
                    'next_step': 'Complete your profile to start creating GST-compliant invoices',
                    'trial_info': {
                        'trial_active': user.is_trial_active,
                        'trial_ends_at': user.trial_ends_at,
                        'days_remaining': (user.trial_ends_at - timezone.now()).days if user.trial_ends_at else 30
                    }
                }, status=status.HTTP_201_CREATED)
                
        except IntegrityError:
            return Response({
                'success': False,
                'error': 'A user with this email or phone already exists.'
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'success': False,
                'error': 'Registration failed. Please try again later.'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response({
        'success': False,
        'errors': serializer.errors
    }, status=status.HTTP_400_BAD_REQUEST)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="User profile with business details and subscription info",
        examples={
            "application/json": {
                "id": "uuid",
                "email": "user@example.com",
                "business_name": "My Shop",
                "phone": "9876543210",
                "gstin": "29ABCDE1234F1Z5",
                "business_address": "123 Main St, City",
                "subscription_status": "trial",
                "trial_ends_at": "2025-11-01T10:00:00Z",
                "profile_completed": True,
                "can_generate_gst_invoice": True,
                "is_trial_active": True
            }
        }
    )}
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile_view(request):
    """Get complete user profile with business and subscription details"""
    user = request.user
    user.last_login_at = timezone.now()
    user.save(update_fields=['last_login_at'])
    
    serializer = UserProfileSerializer(user)
    return Response({
        'success': True,
        'profile': serializer.data,
        'setup_progress': {
            'signup_completed': True,
            'profile_completed': user.profile_completed,
            'can_create_invoices': bool(user.business_name),
            'can_create_gst_invoices': user.can_generate_gst_invoice,
            'next_steps': _get_profile_next_steps(user)
        }
    })

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
def password_reset_request_view(request):
    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)
    User = get_user_model()
    try:
        user = User.objects.get(email=email)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}/"
        send_mail(
            subject="Password Reset Request",
            message=f"Click the link to reset your password: {reset_link}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
        return Response({'message': 'Password reset link sent.'})
    except User.DoesNotExist:
        # Do not reveal if email exists for security
        return Response({'message': 'Password reset link sent.'})

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


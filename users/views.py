from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import AnonRateThrottle
from django.db import IntegrityError, transaction
from .serializers import RegisterSerializer, ProfileSerializer, CustomTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
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
    request_body=RegisterSerializer,
    responses={201: openapi.Response(
        description="User registered",
        examples={
            "application/json": {
                "message": "User registered successfully."
            }
        }
    ), 400: openapi.Response(
        description="Validation error",
        examples={
            "application/json": {
                "error": "A user with this email or phone already exists."
            }
        }
    )}
)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegisterRateThrottle])
def register_view(request):
    serializer = RegisterSerializer(data=request.data)
    if serializer.is_valid():
        try:
            with transaction.atomic():
                serializer.save()
            return Response({'message': 'User registered successfully.'}, status=status.HTTP_201_CREATED)
        except IntegrityError as e:
            return Response({'error': 'A user with this email or phone already exists.'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': 'Registration failed. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@swagger_auto_schema(
    method='get',
    responses={200: openapi.Response(
        description="User profile",
        examples={
            "application/json": {
                "id": 1,
                "email": "user@example.com",
                "name": "John Doe",
                "phone": "9876543210"
            }
        }
    )}
)
@api_view(['GET', 'PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def profile_view(request):
    user = request.user
    if request.method == 'GET':
        serializer = ProfileSerializer(user)
        return Response(serializer.data)
    elif request.method in ['PUT', 'PATCH']:
        serializer = ProfileSerializer(user, data=request.data, partial=(request.method == 'PATCH'))
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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


from rest_framework import serializers
from .models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.utils import timezone
from datetime import timedelta

class QuickSignupSerializer(serializers.ModelSerializer):
    """Minimal friction signup - only essential fields"""
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ('email', 'password', 'confirm_password', 'phone', 'business_name', 'gstin')
        extra_kwargs = {
            'gstin': {'required': False, 'allow_blank': True},
        }
    
    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError("Passwords don't match")
        return attrs
    
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already registered")
        return value
    
    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Phone number already registered")
        return value
    
    def create(self, validated_data):
        # Remove confirm_password from validated_data
        validated_data.pop('confirm_password')
        
        # Use email as username for simplicity
        validated_data['username'] = validated_data['email']
        
        # Set trial period (30 days from signup)
        trial_end = timezone.now() + timedelta(days=30)
        
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            phone=validated_data['phone'],
            business_name=validated_data['business_name'],
            gstin=validated_data.get('gstin', ''),
            password=validated_data['password'],
            trial_ends_at=trial_end
        )
        return user

class ProfileSetupSerializer(serializers.ModelSerializer):
    """Complete profile setup - done inside the app"""
    
    class Meta:
        model = User
        fields = (
            'first_name', 'last_name', 'business_name', 'business_address', 
            'gstin', 'phone'
        )
        extra_kwargs = {
            'business_name': {'required': True},
            'business_address': {'required': False},
        }
    
    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        # Check if profile should be marked as completed
        instance.mark_profile_completed()
        return instance

class UserProfileSerializer(serializers.ModelSerializer):
    """Full user profile for display"""
    can_generate_gst_invoice = serializers.ReadOnlyField()
    is_trial_active = serializers.ReadOnlyField()
    
    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'phone', 'first_name', 'last_name',
            'business_name', 'business_address', 'gstin', 'subscription_status',
            'trial_ends_at', 'profile_completed', 'can_generate_gst_invoice', 
            'is_trial_active', 'date_joined', 'last_login_at'
        )
        read_only_fields = (
            'id', 'username', 'subscription_status', 'trial_ends_at', 
            'profile_completed', 'can_generate_gst_invoice', 'is_trial_active',
            'date_joined', 'last_login_at'
        )

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Add custom claims if needed
        token['username'] = user.username
        token['email'] = user.email
        return token
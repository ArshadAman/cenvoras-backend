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

class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Comprehensive profile update serializer"""
    current_password = serializers.CharField(write_only=True, required=False, help_text="Required only when changing email or password")
    new_password = serializers.CharField(write_only=True, required=False, min_length=8)
    confirm_new_password = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = User
        fields = (
            'first_name', 'last_name', 'phone', 'business_name', 
            'business_address', 'gstin', 'email', 'current_password',
            'new_password', 'confirm_new_password'
        )
        extra_kwargs = {
            'phone': {'required': False},
            'business_name': {'required': False},
            'email': {'required': False},
        }
    
    def validate(self, attrs):
        user = self.instance
        
        # Password change validation
        new_password = attrs.get('new_password')
        confirm_new_password = attrs.get('confirm_new_password')
        current_password = attrs.get('current_password')
        
        if new_password:
            if not confirm_new_password:
                raise serializers.ValidationError({
                    'confirm_new_password': 'This field is required when setting a new password.'
                })
            if new_password != confirm_new_password:
                raise serializers.ValidationError({
                    'confirm_new_password': 'New passwords do not match.'
                })
            if not current_password:
                raise serializers.ValidationError({
                    'current_password': 'Current password is required to set a new password.'
                })
            if not user.check_password(current_password):
                raise serializers.ValidationError({
                    'current_password': 'Current password is incorrect.'
                })
        
        # Email change validation
        email = attrs.get('email')
        if email and email != user.email:
            if not current_password:
                raise serializers.ValidationError({
                    'current_password': 'Current password is required to change email.'
                })
            if not user.check_password(current_password):
                raise serializers.ValidationError({
                    'current_password': 'Current password is incorrect.'
                })
            if User.objects.filter(email=email).exclude(id=user.id).exists():
                raise serializers.ValidationError({
                    'email': 'A user with this email already exists.'
                })
        
        # Phone validation
        phone = attrs.get('phone')
        if phone and phone != user.phone:
            if User.objects.filter(phone=phone).exclude(id=user.id).exists():
                raise serializers.ValidationError({
                    'phone': 'A user with this phone number already exists.'
                })
        
        return attrs
    
    def update(self, instance, validated_data):
        # Remove password fields from validated_data for normal update
        current_password = validated_data.pop('current_password', None)
        new_password = validated_data.pop('new_password', None)
        confirm_new_password = validated_data.pop('confirm_new_password', None)
        
        # Update regular fields
        instance = super().update(instance, validated_data)
        
        # Handle password change
        if new_password:
            instance.set_password(new_password)
            instance.save(update_fields=['password'])
        
        # Handle email change (update username too)
        if 'email' in validated_data:
            instance.username = instance.email
            instance.save(update_fields=['username'])
        
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
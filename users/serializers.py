from rest_framework import serializers
from .models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.utils import timezone
from datetime import timedelta
from subscription.services import get_tenant_plan, get_effective_plan_code, get_effective_limit, is_vip_user

class QuickSignupSerializer(serializers.ModelSerializer):
    """Minimal friction signup - only essential fields"""
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ('email', 'password', 'confirm_password', 'phone', 'business_name', 'country', 'trn', 'gstin', 'state', 'city')
        extra_kwargs = {
            'gstin': {'required': False, 'allow_blank': True, 'allow_null': True},
            'trn': {'required': False, 'allow_blank': True, 'allow_null': True},
            'country': {'required': False},
            'state': {'required': False, 'allow_blank': True, 'allow_null': True},
            'city': {'required': False, 'allow_blank': True, 'allow_null': True},
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
        
        # Set trial period (14 days from signup)
        trial_end = timezone.now() + timedelta(days=14)
        
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            phone=validated_data['phone'],
            business_name=validated_data['business_name'],
            country=validated_data.get('country', 'IN'),
            gstin=validated_data.get('gstin', ''),
            trn=validated_data.get('trn', ''),
            state=validated_data.get('state'),
            city=validated_data.get('city'),
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
            'gstin', 'pan_number', 'gem_id', 'dl_number', 'phone', 'state', 'city',
            'country', 'trn', 'bank_name', 'bank_account_number', 'bank_ifsc_code',
            'bank_branch', 'bank_upi_id', 'bank_qr_code'
        )
        extra_kwargs = {
            'business_name': {'required': True},
            'business_address': {'required': False},
            'gstin': {'required': False, 'allow_blank': True, 'allow_null': True},
            'pan_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'gem_id': {'required': False, 'allow_blank': True, 'allow_null': True},
            'dl_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'state': {'required': False, 'allow_blank': True, 'allow_null': True},
            'city': {'required': False, 'allow_blank': True, 'allow_null': True},
            'country': {'required': False},
            'trn': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_name': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_account_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_ifsc_code': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_branch': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_upi_id': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_qr_code': {'required': False, 'allow_blank': True, 'allow_null': True},
        }
    
    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        # Check if profile should be marked as completed
        instance.mark_profile_completed()
        return instance

class UserProfileSerializer(serializers.ModelSerializer):
    """Full user profile for display"""
    is_trial_active = serializers.SerializerMethodField()
    profile_completed = serializers.SerializerMethodField()
    can_generate_gst_invoice = serializers.SerializerMethodField()
    parent_business_name = serializers.CharField(source='parent.business_name', read_only=True)
    plan_name = serializers.SerializerMethodField()
    plan_code = serializers.SerializerMethodField()
    max_managers = serializers.SerializerMethodField()

    def get_is_trial_active(self, obj):
        return obj.is_trial_active

    def get_profile_completed(self, obj):
        return obj.profile_completed

    def get_can_generate_gst_invoice(self, obj):
        return obj.can_generate_gst_invoice
        
    def get_plan_name(self, obj):
        if is_vip_user(obj):
            return "Business"
        
        plan_code = get_effective_plan_code(obj)
        plan = get_tenant_plan(obj)
        
        if plan:
            return plan.name
            
        # Fallback for trial users or other states without a Plan object
        if plan_code == 'pro':
            return "Pro"
        if plan_code == 'business':
            return "Business"
        return "Starter"

    def get_plan_code(self, obj):
        return get_effective_plan_code(obj)
        
    def get_max_managers(self, obj):
        return get_effective_limit(obj, 'max_team_members', 0)

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'phone', 'first_name', 'last_name',
            'business_name', 'invoice_prefix', 'quotation_prefix', 'delivery_challan_prefix', 'business_address', 'gstin', 'pan_number', 'gem_id', 'dl_number', 
            'state', 'city', 'subscription_status',
            'subscription_tier', 'permissions',
            'trial_ends_at', 'profile_completed', 'can_generate_gst_invoice', 
            'is_trial_active', 'date_joined', 'last_login_at', 'role',
            'parent_business_name', 'plan_name', 'plan_code', 'max_managers',
            'country', 'currency', 'trn', 'is_vat_registered',
            'bank_name', 'bank_account_number', 'bank_ifsc_code', 'bank_branch',
            'bank_upi_id', 'bank_qr_code', 'bank_accounts', 'default_bank_account_sections', 'default_preview_template_sections'
        )
        read_only_fields = (
            'id', 'username', 'subscription_status', 'subscription_tier', 'permissions', 'trial_ends_at', 
            'profile_completed', 'can_generate_gst_invoice', 'is_trial_active',
            'date_joined', 'last_login_at', 'role'
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # Synthesize Account 1 from legacy fields if bank_accounts is empty
        if not ret.get('bank_accounts') and (instance.bank_name or instance.bank_account_number):
            ret['bank_accounts'] = [{
                'id': 'bank_acc_1',
                'account_name': 'Primary Account',
                'bank_name': instance.bank_name or '',
                'account_number': instance.bank_account_number or '',
                'ifsc_code': instance.bank_ifsc_code or '',
                'account_holder': instance.business_name or '',
                'branch': instance.bank_branch or '',
                'upi_id': instance.bank_upi_id or '',
                'qr_code': instance.bank_qr_code or '',
                'is_default': True,
            }]
        return ret

class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Comprehensive profile update serializer"""
    current_password = serializers.CharField(write_only=True, required=False, help_text="Required when changing email, password, or bank details")
    new_password = serializers.CharField(write_only=True, required=False, min_length=8)
    confirm_new_password = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'phone', 'business_name', 
            'invoice_prefix', 'quotation_prefix', 'delivery_challan_prefix', 'business_address', 'gstin', 'pan_number', 'gem_id', 'dl_number', 
            'state', 'city', 'email', 'current_password',
            'new_password', 'confirm_new_password',
            'country', 'currency', 'trn', 'is_vat_registered',
            'bank_name', 'bank_account_number', 'bank_ifsc_code', 'bank_branch',
            'bank_upi_id', 'bank_qr_code', 'bank_accounts', 'default_bank_account_sections', 'default_preview_template_sections'
        ]
        extra_kwargs = {
            'phone': {'required': False},
            'business_name': {'required': False},
            'invoice_prefix': {'required': False},
            'quotation_prefix': {'required': False},
            'email': {'required': False},
            'gstin': {'required': False, 'allow_blank': True, 'allow_null': True},
            'pan_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'gem_id': {'required': False, 'allow_blank': True, 'allow_null': True},
            'dl_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'state': {'required': False, 'allow_blank': True, 'allow_null': True},
            'city': {'required': False, 'allow_blank': True, 'allow_null': True},
            'trn': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_name': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_account_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_ifsc_code': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_branch': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_upi_id': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_qr_code': {'required': False, 'allow_blank': True, 'allow_null': True},
            'bank_accounts': {'required': False},
            'default_bank_account_sections': {'required': False},
            'default_preview_template_sections': {'required': False},
        }

    def validate_invoice_prefix(self, value):
        normalized = str(value or '').strip().upper()
        return normalized or 'INV'

    def validate_quotation_prefix(self, value):
        normalized = str(value or '').strip().upper()
        return normalized or 'QT'

    def validate_delivery_challan_prefix(self, value):
        normalized = str(value or '').strip().upper()
        return normalized or 'DC'

    def validate_bank_accounts(self, value):
        if not value:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("bank_accounts must be a list of accounts.")
        if len(value) > 3:
            raise serializers.ValidationError("A maximum of 3 bank accounts can be configured.")

        cleaned = []
        for idx, acc in enumerate(value):
            if not isinstance(acc, dict):
                continue
            cleaned_acc = {
                'id': str(acc.get('id') or f'bank_acc_{idx+1}'),
                'account_name': str(acc.get('account_name') or f'Account {idx+1}').strip(),
                'bank_name': str(acc.get('bank_name') or '').strip(),
                'account_number': str(acc.get('account_number') or '').strip(),
                'ifsc_code': str(acc.get('ifsc_code') or '').strip().upper(),
                'account_holder': str(acc.get('account_holder') or '').strip(),
                'branch': str(acc.get('branch') or '').strip(),
                'upi_id': str(acc.get('upi_id') or '').strip(),
                'qr_code': str(acc.get('qr_code') or '').strip(),
                'is_default': bool(acc.get('is_default', False if idx > 0 else True)),
            }
            cleaned.append(cleaned_acc)

        # Ensure at least one account is marked default if list is non-empty
        if cleaned and not any(a.get('is_default') for a in cleaned):
            cleaned[0]['is_default'] = True

        return cleaned
    
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

        # Bank details validation (Requires current password to update)
        bank_fields = [
            'bank_name', 'bank_account_number', 'bank_ifsc_code',
            'bank_branch', 'bank_upi_id', 'bank_qr_code'
        ]
        bank_fields_changed = any(
            field in attrs and (attrs[field] or '') != (getattr(user, field, '') or '')
            for field in bank_fields
        ) or ('bank_accounts' in attrs and attrs['bank_accounts'] != (getattr(user, 'bank_accounts', []) or []))
        if bank_fields_changed:
            if not current_password:
                raise serializers.ValidationError({
                    'current_password': 'Current password is required to update bank details.'
                })
            if not user.check_password(current_password):
                raise serializers.ValidationError({
                    'current_password': 'Current password is incorrect.'
                })
        
        # Phone validation
        phone = attrs.get('phone')
        if phone and phone != user.phone:
            if User.objects.filter(phone=phone).exclude(id=user.id).exists():
                raise serializers.ValidationError({
                    'phone': 'A user with this phone number already exists.'
                })
        
        # TRN Validation
        country = attrs.get('country', user.country)
        trn = attrs.get('trn', user.trn)
        
        if country == 'AE' and trn:
            if len(str(trn)) != 15 or not str(trn).isdigit():
                raise serializers.ValidationError({
                    'trn': 'UAE Tax Registration Number (TRN) must be exactly 15 digits.'
                })
        
        return attrs
    
    def update(self, instance, validated_data):
        # Remove password fields from validated_data for normal update
        current_password = validated_data.pop('current_password', None)
        new_password = validated_data.pop('new_password', None)
        confirm_new_password = validated_data.pop('confirm_new_password', None)
        
        # Update regular fields
        instance = super().update(instance, validated_data)

        # Synchronize default/first bank account to legacy fields for 100% backward compatibility
        bank_accounts = validated_data.get('bank_accounts')
        if bank_accounts is not None:
            default_acc = next((a for a in bank_accounts if a.get('is_default')), None)
            if not default_acc and bank_accounts:
                default_acc = bank_accounts[0]
            if default_acc:
                instance.bank_name = default_acc.get('bank_name', '')
                instance.bank_account_number = default_acc.get('account_number', '')
                instance.bank_ifsc_code = default_acc.get('ifsc_code', '')
                instance.bank_branch = default_acc.get('branch', '')
                instance.bank_upi_id = default_acc.get('upi_id', '')
                if default_acc.get('qr_code'):
                    instance.bank_qr_code = default_acc.get('qr_code')
                instance.save(update_fields=[
                    'bank_name', 'bank_account_number', 'bank_ifsc_code',
                    'bank_branch', 'bank_upi_id', 'bank_qr_code'
                ])
        
        # Handle password change
        if new_password:
            instance.set_password(new_password)
            instance.save(update_fields=['password'])
        
        # Handle email change (update username too)
        if 'email' in validated_data:
            instance.username = instance.email
            instance.save(update_fields=['username'])
            
        return instance
        


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Add custom claims if needed
        token['username'] = user.username
        token['email'] = user.email
        token['role'] = user.role
        return token

class TeamMemberSerializer(serializers.ModelSerializer):
    """Admin-only serializer for spawning Team Members (Managers, Salesmen)"""
    password = serializers.CharField(write_only=True, min_length=8, required=False)
    
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'phone', 'role', 'permissions', 'password', 'date_joined')
        read_only_fields = ('id', 'date_joined')
        
    def validate_email(self, value):
        qs = User.objects.filter(email=value)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def create(self, validated_data):
        admin_user = self.context['request'].user
        
        # Enforce that the creator is an Admin
        if admin_user.role != 'admin':
            raise serializers.ValidationError("Only Admins can spawn team members.")
            
        validated_data['username'] = validated_data['email']
        password = validated_data.pop('password')
        
        # Set parent to the tenant owner
        parent = admin_user.active_tenant
        
        member = User.objects.create_user(
            parent=parent,
            **validated_data
        )
        member.set_password(password)
        member.save()
        return member

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save()
        return instance
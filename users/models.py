from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', 'Trial'
    ACTIVE = 'active', 'Active'
    EXPIRED = 'expired', 'Expired'
    CANCELLED = 'cancelled', 'Cancelled'

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Core signup fields
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True, help_text="Phone number for login recovery and communication")
    business_name = models.CharField(max_length=100, blank=True, null=True, help_text="Business/Shop name (appears on invoices)")
    invoice_prefix = models.CharField(max_length=20, default='INV-', help_text="Default invoice prefix for this user")
    
    # Optional fields (can be added later)
    gstin = models.CharField(max_length=15, blank=True, null=True, help_text="GST Identification Number (optional)")
    gem_id = models.CharField(max_length=50, blank=True, null=True, help_text="GEM ID (optional)")
    dl_number = models.CharField(max_length=50, blank=True, null=True, help_text="DL number (optional)")
    business_address = models.TextField(blank=True, null=True, help_text="Complete business address for invoices")
    
    from cenvoras.constants import IndianStates
    state = models.CharField(
        max_length=2, 
        choices=IndianStates.choices, 
        blank=True, 
        null=True, 
        help_text="State for tax calculation (Place of Supply)"
    )
    
    # System fields
    subscription_status = models.CharField(
        max_length=20, 
        choices=SubscriptionStatus.choices, 
        default=SubscriptionStatus.TRIAL,
        help_text="Current subscription status"
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True, help_text="When trial period ends")
    is_lifetime_free = models.BooleanField(default=False, help_text="Account is permanently free (VIP/Friends)")
    last_login_at = models.DateTimeField(null=True, blank=True, help_text="Last login timestamp")
    
    # Profile completion tracking
    profile_completed = models.BooleanField(default=False, help_text="Has user completed profile setup")
    
    # Feature 90: Role Based Access Control
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('salesman', 'Salesman'),
        ('accountant', 'Accountant'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='admin', help_text="User capability role")
    
    # Feature 90: RBAC and Subscriptions
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='team')
    
    SUBSCRIPTION_TIERS = (
        ('FREE', 'Free'),
        ('MID', 'Mid'),
        ('PRO', 'Pro'),
    )
    subscription_tier = models.CharField(max_length=10, choices=SUBSCRIPTION_TIERS, default='FREE', help_text="Subscription tier for limits")
    
    permissions = models.JSONField(default=dict, blank=True, help_text="Granular permissions for managers")
    
    def __str__(self):
        return f"{self.business_name} ({self.username})" if self.business_name else self.username
    
    @property
    def is_trial_active(self):
        """Check if user is in active trial period"""
        tenant = self.active_tenant
        if tenant.is_lifetime_free:
            return False  # Not in trial, they're free forever
        if tenant.subscription_status != SubscriptionStatus.TRIAL:
            return False
        if not tenant.trial_ends_at:
            return True  # No expiry set yet
        from django.utils import timezone
        return timezone.now() < tenant.trial_ends_at
    
    @property
    def has_active_subscription(self):
        """Check if user has access to full features (paid, trial, or lifetime free)"""
        tenant = self.active_tenant
        if tenant.is_lifetime_free:
            return True  # VIP - always has access
        if tenant.subscription_status == SubscriptionStatus.ACTIVE:
            return True
        if self.is_trial_active:
            return True
        return False
    
    @property
    def active_tenant(self):
        """Returns the parent if this is a manager/team member, otherwise self."""
        return self.parent if self.parent else self

    @property
    def can_generate_gst_invoice(self):
        """Check if user can generate GST-compliant invoices"""
        tenant = self.active_tenant
        return bool(tenant.gstin and tenant.business_address)
    
    def mark_profile_completed(self):
        """Mark profile as completed if all required fields are filled"""
        required_fields = [self.business_name, self.business_address]
        if all(required_fields):
            self.profile_completed = True
            self.save(update_fields=['profile_completed'])

class ActionLog(models.Model):
    """
    Audit Trail for critical actions (Enterprise Requirement)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50) # CREATE, UPDATE, DELETE
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - {self.action} {self.model_name} at {self.timestamp}"

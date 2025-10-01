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
    phone = models.CharField(max_length=15, unique=True, help_text="Phone number for login recovery and communication")
    business_name = models.CharField(max_length=100, blank=True, null=True, help_text="Business/Shop name (appears on invoices)")
    
    # Optional fields (can be added later)
    gstin = models.CharField(max_length=15, blank=True, null=True, help_text="GST Identification Number (optional)")
    business_address = models.TextField(blank=True, null=True, help_text="Complete business address for invoices")
    
    # System fields
    subscription_status = models.CharField(
        max_length=20, 
        choices=SubscriptionStatus.choices, 
        default=SubscriptionStatus.TRIAL,
        help_text="Current subscription status"
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True, help_text="When trial period ends")
    last_login_at = models.DateTimeField(null=True, blank=True, help_text="Last login timestamp")
    
    # Profile completion tracking
    profile_completed = models.BooleanField(default=False, help_text="Has user completed profile setup")
    
    def __str__(self):
        return f"{self.business_name} ({self.username})" if self.business_name else self.username
    
    @property
    def is_trial_active(self):
        """Check if user is in active trial period"""
        if self.subscription_status != SubscriptionStatus.TRIAL:
            return False
        if not self.trial_ends_at:
            return True  # No expiry set yet
        from django.utils import timezone
        return timezone.now() < self.trial_ends_at
    
    @property
    def can_generate_gst_invoice(self):
        """Check if user can generate GST-compliant invoices"""
        return bool(self.gstin and self.business_address)
    
    def mark_profile_completed(self):
        """Mark profile as completed if all required fields are filled"""
        required_fields = [self.business_name, self.business_address]
        if all(required_fields):
            self.profile_completed = True
            self.save(update_fields=['profile_completed'])

from django.db import models
from django.conf import settings
from django.utils import timezone

class Feature(models.Model):
    """
    Specific capabilities that can be bundled into a Plan.
    e.g., 'max_managers', 'export_ledgers', 'ai_insights'
    """
    code = models.CharField(max_length=50, unique=True, help_text="System code for the feature")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    def __str__(self):
        return self.name

class Plan(models.Model):
    """
    A Tiered Packaging option (e.g. Starter, Growth, Enterprise)
    """
    code = models.CharField(max_length=50, unique=True, help_text="e.g. starter, growth")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    yearly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Hard Limits built-in for fast access
    max_managers = models.IntegerField(default=0, help_text="Number of staff accounts allowed")
    max_customers = models.IntegerField(default=-1, help_text="-1 for unlimited customers")
    max_team_members = models.IntegerField(default=0, help_text="Number of team members allowed")
    max_invoices_per_month = models.IntegerField(default=-1, help_text="-1 for unlimited")
    
    features = models.ManyToManyField(Feature, blank=True, related_name='plans')
    
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.name} (₹{self.monthly_price}/mo)"

    @property
    def effective_team_limit(self):
        return self.max_team_members if self.max_team_members is not None else self.max_managers

    @property
    def is_free(self):
        return self.code in {'free', 'starter'}

    @property
    def is_pro(self):
        return self.code in {'pro', 'growth'}

    @property
    def is_business(self):
        return self.code in {'business', 'enterprise'}

class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', 'Trial'
    ACTIVE = 'active', 'Active'
    PAST_DUE = 'past_due', 'Past Due'
    CANCELLED = 'cancelled', 'Cancelled'

class TenantSubscription(models.Model):
    """
    Binds a User (Tenant) to a specific Plan and tracks its validity window.
    """
    tenant = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    
    status = models.CharField(
        max_length=20, 
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL
    )
    
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    
    stripe_customer_id = models.CharField(max_length=100, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.tenant.username} - {self.plan.name} ({self.status})"
        
    @property
    def is_valid(self):
        if self.status in [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]:
            if self.current_period_end:
                return timezone.now() < self.current_period_end
            return True
        return False

    @property
    def effective_plan_code(self):
        return self.plan.code if self.plan else 'free'

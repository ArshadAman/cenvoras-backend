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
    quarterly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
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

    def get_price_for_cycle(self, cycle: str) -> Decimal:
        from decimal import Decimal, ROUND_HALF_UP
        if cycle == BillingCycle.QUARTERLY:
            if self.quarterly_price > 0:
                return self.quarterly_price
            # Default logic: Rate * 3 - 15%
            amount = self.monthly_price * Decimal('3') * Decimal('0.85')
            return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif cycle == BillingCycle.YEARLY:
            if self.yearly_price > 0:
                return self.yearly_price
            # Default logic: Rate * 12 - 30%
            amount = self.monthly_price * Decimal('12') * Decimal('0.70')
            return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return self.monthly_price

class SubscriptionStatus(models.TextChoices):
    TRIAL = 'trial', 'Trial'
    ACTIVE = 'active', 'Active'
    PAST_DUE = 'past_due', 'Past Due'
    CANCELLED = 'cancelled', 'Cancelled'

class BillingCycle(models.TextChoices):
    MONTHLY = 'monthly', 'Monthly'
    QUARTERLY = 'quarterly', 'Quarterly'
    YEARLY = 'yearly', 'Yearly'


class SubscriptionPaymentStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    SUCCESS = 'success', 'Success'
    FAILED = 'failed', 'Failed'


class SubscriptionPaymentAction(models.TextChoices):
    ACTIVATE = 'activate', 'Activate'
    RENEW = 'renew', 'Renew'
    UPGRADE_NOW = 'upgrade_now', 'Upgrade Now'

class TenantSubscription(models.Model):
    """
    Binds a User (Tenant) to a specific Plan and tracks its validity window.
    """
    tenant = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY
    )
    
    status = models.CharField(
        max_length=20, 
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL
    )
    
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    pending_plan = models.ForeignKey('Plan', on_delete=models.SET_NULL, null=True, blank=True, related_name='pending_subscriptions')
    pending_plan_starts_at = models.DateTimeField(null=True, blank=True)
    
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


class SubscriptionPayment(models.Model):
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription_payments')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscription_payments')

    provider = models.CharField(max_length=30, default='cashfree')
    order_id = models.CharField(max_length=64, unique=True)
    cf_order_id = models.CharField(max_length=64, blank=True, null=True)
    payment_session_id = models.TextField(blank=True, null=True)
    cf_payment_id = models.CharField(max_length=64, blank=True, null=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(max_length=20, choices=SubscriptionPaymentStatus.choices, default=SubscriptionPaymentStatus.PENDING)
    action = models.CharField(max_length=20, choices=SubscriptionPaymentAction.choices, default=SubscriptionPaymentAction.ACTIVATE)
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    source_plan_code = models.CharField(max_length=50, blank=True, null=True)
    billing_details = models.JSONField(default=dict, blank=True)

    raw_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tenant} - {self.plan.code} - {self.order_id} ({self.status})"


class WebhookEvent(models.Model):
    """
    Tracks incoming webhooks from Cashfree to ensure idempotent processing.
    Prevents duplicate subscription updates if a webhook is received multiple times.
    """
    event_id = models.CharField(max_length=200, unique=True, help_text="Unique event ID from Cashfree webhook")
    provider = models.CharField(max_length=30, default='cashfree')
    event_type = models.CharField(max_length=100, help_text="e.g., PAYMENT_SUCCESS, PAYMENT_FAILED")
    order_id = models.CharField(max_length=64, blank=True, null=True)
    payload = models.JSONField(default=dict, blank=True)
    
    processed = models.BooleanField(default=False, help_text="Whether this event has been processed")
    error_message = models.TextField(blank=True, null=True, help_text="Error during processing, if any")
    
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['event_id'], name='sub_wh_ev_id_idx'),
            models.Index(fields=['order_id'], name='sub_wh_ord_id_idx'),
            models.Index(fields=['processed'], name='sub_wh_proc_idx'),
        ]
    
    def __str__(self):
        return f"{self.event_type} - {self.event_id} ({self.provider})"

from decimal import Decimal, ROUND_HALF_UP

from django.db import models
from django.conf import settings
from django.utils import timezone


class BillingCycle(models.TextChoices):
    MONTHLY = 'monthly', 'Monthly'
    QUARTERLY = 'quarterly', 'Quarterly'
    YEARLY = 'yearly', 'Yearly'


CYCLE_MULTIPLIERS = {
    BillingCycle.MONTHLY: Decimal('1'),
    BillingCycle.QUARTERLY: Decimal('3'),
    BillingCycle.YEARLY: Decimal('12'),
}

CYCLE_DISCOUNTS = {
    BillingCycle.MONTHLY: Decimal('0.00'),
    BillingCycle.QUARTERLY: Decimal('0.15'),
    BillingCycle.YEARLY: Decimal('0.30'),
}


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

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
    original_monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    original_quarterly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    original_yearly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # Hard Limits built-in for fast access
    max_managers = models.IntegerField(default=0, help_text="Number of staff accounts allowed")
    max_customers = models.IntegerField(default=-1, help_text="-1 for unlimited customers")
    max_team_members = models.IntegerField(default=0, help_text="Number of team members allowed")
    max_invoices_per_month = models.IntegerField(default=-1, help_text="-1 for unlimited")
    
    features = models.ManyToManyField(Feature, blank=True, related_name='plans')
    
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.name} (₹{self.monthly_price}/mo)"

    def get_base_monthly_price(self):
        """Helper to return the correct base price even if DB is out of sync."""
        code = str(self.code).lower()
        if code == 'pro':
            return Decimal('1599.00')
        if code == 'business':
            return Decimal('1999.00')
        return self.monthly_price

    def price_for_cycle(self, cycle: str):
        normalized = str(cycle or BillingCycle.MONTHLY).lower()
        base = self.get_base_monthly_price()
        if normalized == BillingCycle.YEARLY:
            return money(base * CYCLE_MULTIPLIERS[BillingCycle.YEARLY] * (Decimal('1') - CYCLE_DISCOUNTS[BillingCycle.YEARLY]))
        if normalized == BillingCycle.QUARTERLY:
            return money(base * CYCLE_MULTIPLIERS[BillingCycle.QUARTERLY] * (Decimal('1') - CYCLE_DISCOUNTS[BillingCycle.QUARTERLY]))
        return base

    def original_price_for_cycle(self, cycle: str):
        normalized = str(cycle or BillingCycle.MONTHLY).lower()
        base = self.get_base_monthly_price()
        if normalized == BillingCycle.YEARLY:
            return money(base * CYCLE_MULTIPLIERS[BillingCycle.YEARLY])
        if normalized == BillingCycle.QUARTERLY:
            return money(base * CYCLE_MULTIPLIERS[BillingCycle.QUARTERLY])
        return base

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
    current_billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    pending_plan = models.ForeignKey('Plan', on_delete=models.SET_NULL, null=True, blank=True, related_name='pending_subscriptions')
    pending_billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        null=True,
        blank=True,
    )
    pending_plan_starts_at = models.DateTimeField(null=True, blank=True)
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


class SubscriptionPaymentOrder(models.Model):
    class OrderStatus(models.TextChoices):
        CREATED = 'created', 'Created'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription_payment_orders')
    order_id = models.CharField(max_length=120, unique=True)
    payment_session_id = models.CharField(max_length=255, blank=True, default='')
    target_plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='payment_orders')
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    duration_days = models.PositiveIntegerField(default=30)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.CREATED)
    failure_reason = models.TextField(blank=True, default='')
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.order_id} - {self.target_plan.code} ({self.status})"

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from billing.models import Customer, SalesInvoice
from users.models import User

PLAN_CODE_ALIASES = {
    'starter': 'free',
    'free': 'free',
    'growth': 'pro',
    'pro': 'pro',
    'enterprise': 'business',
    'business': 'business',
}

MODULE_FEATURES = {
    'inventory': 'inventory_core',
    'analytics': 'advanced_analytics',
    'integrations': 'integrations',
    'dashboard': 'dashboard_analytics',
    'reports': 'advanced_reports',
    'forecast': 'sales_forecast',
    'restock': 'restock_predictions',
    'warehouse': 'multi_warehouse',
    'item_pnl': 'item_wise_pnl',
    'stock_ledger': 'stock_ledger',
    'shortage_management': 'shortage_management',
    'priority_support': 'priority_support',
    'team': 'team_management',
}


def normalize_plan_code(code: str | None) -> str:
    return PLAN_CODE_ALIASES.get((code or 'free').strip().lower(), 'free')


def get_tenant(user: User) -> User:
    return getattr(user, 'active_tenant', user)


def is_vip_user(user: User) -> bool:
    tenant = get_tenant(user)
    return bool(getattr(tenant, 'is_lifetime_free', False))


def get_tenant_subscription(user: User):
    tenant = get_tenant(user)
    return getattr(tenant, 'subscription', None)


def _is_legacy_trial_active(tenant: User) -> bool:
    status = (getattr(tenant, 'subscription_status', '') or '').lower()
    if status != 'trial':
        return False
    trial_ends_at = getattr(tenant, 'trial_ends_at', None)
    if not trial_ends_at:
        return True
    return timezone.now() < trial_ends_at


def _sync_legacy_status_on_expiry(tenant: User) -> None:
    current_status = (getattr(tenant, 'subscription_status', '') or '').lower()
    if current_status in {'active', 'trial'}:
        tenant.subscription_status = 'expired'
        tenant.subscription_tier = 'FREE'
        tenant.save(update_fields=['subscription_status', 'subscription_tier'])


def get_active_tenant_subscription(user: User):
    subscription = get_tenant_subscription(user)
    if not subscription:
        return None

    now = timezone.now()

    # Prepaid behavior: if a next plan was purchased earlier, activate it automatically
    # when its start time arrives.
    if subscription.pending_plan and subscription.pending_plan_starts_at and now >= subscription.pending_plan_starts_at:
        start_time = subscription.pending_plan_starts_at
        subscription.plan = subscription.pending_plan
        subscription.status = 'active'
        subscription.current_period_start = start_time
        subscription.current_period_end = start_time + timedelta(days=30)
        subscription.pending_plan = None
        subscription.pending_plan_starts_at = None
        subscription.save(update_fields=[
            'plan',
            'status',
            'current_period_start',
            'current_period_end',
            'pending_plan',
            'pending_plan_starts_at',
            'updated_at',
        ])

    if not subscription.is_valid:
        if subscription.status in {'active', 'trial'}:
            subscription.status = 'past_due'
            subscription.save(update_fields=['status', 'updated_at'])
        _sync_legacy_status_on_expiry(get_tenant(user))
        return None

    return subscription


def get_tenant_plan(user: User):
    subscription = get_active_tenant_subscription(user)
    if subscription and subscription.plan:
        return subscription.plan
    return None


def get_effective_plan_code(user: User) -> str:
    if is_vip_user(user):
        return 'business'

    plan = get_tenant_plan(user)
    if plan:
        return normalize_plan_code(plan.code)

    tenant = get_tenant(user)
    if not _is_legacy_trial_active(tenant):
        tenant_status = (getattr(tenant, 'subscription_status', '') or '').lower()
        if tenant_status != 'active':
            return 'free'

    return normalize_plan_code(getattr(tenant, 'subscription_tier', 'FREE'))


def get_effective_limit(user: User, field_name: str, default: int = -1) -> int:
    if is_vip_user(user):
        return -1

    plan = get_tenant_plan(user)
    if not plan:
        return default

    value = getattr(plan, field_name, None)
    if value is None and field_name == 'max_team_members':
        value = getattr(plan, 'max_managers', default)
    return default if value is None else int(value)


def get_current_usage(user: User) -> dict[str, int]:
    tenant = get_tenant(user)
    month_start = timezone.now().date().replace(day=1)

    invoices_this_month = SalesInvoice.objects.filter(
        created_by=tenant,
        invoice_date__gte=month_start,
    ).count()

    customers_total = Customer.objects.filter(created_by=tenant).count()
    team_members_total = User.objects.filter(parent=tenant).exclude(id=tenant.id).count()

    return {
        'invoices_this_month': invoices_this_month,
        'customers_total': customers_total,
        'team_members_total': team_members_total,
    }


def can_use_feature(user: User, feature_code: str) -> bool:
    if is_vip_user(user):
        return True

    plan_code = get_effective_plan_code(user)
    feature_code = (feature_code or '').strip().lower()

    if plan_code == 'business':
        return True

    if plan_code == 'pro':
        allowed = {
            'customer_management',
            'basic_invoicing',
            'inventory_core',
            'advanced_analytics',
            'dashboard_analytics',
            'integrations',
            'advanced_reports',
            'team_management',
        }
        return feature_code in allowed

    free_allowed = {
        'customer_management',
        'basic_invoicing',
    }
    return feature_code in free_allowed


def get_entitlements(user: User) -> dict[str, Any]:
    subscription = get_active_tenant_subscription(user)
    plan = subscription.plan if subscription else None
    plan_code = get_effective_plan_code(user)
    tenant = get_tenant(user)
    vip = is_vip_user(user)
    usage = get_current_usage(user)

    limits = {
        'max_team_members': get_effective_limit(user, 'max_team_members', 0),
        'max_invoices_per_month': -1,
        'max_customers': -1,
    }

    locked_modules = {}
    for module_name, feature_code in MODULE_FEATURES.items():
        locked_modules[module_name] = {
            'feature_code': feature_code,
            'enabled': can_use_feature(user, feature_code),
        }

    return {
        'tenant_id': str(tenant.id),
        'plan': {
            'code': plan_code,
            'name': 'VIP Access' if vip else (getattr(plan, 'name', None) if plan else plan_code.title()),
            'status': getattr(subscription, 'status', 'expired' if plan_code == 'free' else None),
            'current_period_end': getattr(subscription, 'current_period_end', None),
            'pending_plan_code': getattr(getattr(subscription, 'pending_plan', None), 'code', None),
            'pending_plan_name': getattr(getattr(subscription, 'pending_plan', None), 'name', None),
            'pending_plan_starts_at': getattr(subscription, 'pending_plan_starts_at', None),
        },
        'limits': limits,
        'usage': usage,
        'locked_modules': locked_modules,
        'can': {
            'inventory': can_use_feature(user, MODULE_FEATURES['inventory']),
            'analytics': can_use_feature(user, MODULE_FEATURES['analytics']),
            'integrations': can_use_feature(user, MODULE_FEATURES['integrations']),
            'dashboard': can_use_feature(user, MODULE_FEATURES['dashboard']),
            'reports': can_use_feature(user, MODULE_FEATURES['reports']),
            'forecast': can_use_feature(user, MODULE_FEATURES['forecast']),
            'restock': can_use_feature(user, MODULE_FEATURES['restock']),
            'warehouse': can_use_feature(user, MODULE_FEATURES['warehouse']),
            'item_pnl': can_use_feature(user, MODULE_FEATURES['item_pnl']),
            'stock_ledger': can_use_feature(user, MODULE_FEATURES['stock_ledger']),
            'shortage_management': can_use_feature(user, MODULE_FEATURES['shortage_management']),
            'priority_support': can_use_feature(user, MODULE_FEATURES['priority_support']),
            'team': can_use_feature(user, MODULE_FEATURES['team']),
        },
    }


def get_limit_exceeded_reason(user: User, resource: str) -> str | None:
    entitlements = get_entitlements(user)
    usage = entitlements['usage']
    limits = entitlements['limits']

    if resource == 'team' and limits['max_team_members'] >= 0 and usage['team_members_total'] >= limits['max_team_members']:
        return 'You have reached your team member limit.'
    return None

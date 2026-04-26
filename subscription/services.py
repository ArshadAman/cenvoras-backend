from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from decimal import Decimal

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


def get_tenant_plan(user: User):
    subscription = get_tenant_subscription(user)
    if subscription and subscription.plan:
        return subscription.plan
    return None


def get_effective_plan_code(user: User) -> str:
    if is_vip_user(user):
        return 'business'

    tenant = get_tenant(user)
    subscription = get_tenant_subscription(user)
    plan = getattr(subscription, 'plan', None) if subscription else None
    if plan:
        plan_code = normalize_plan_code(plan.code)
        if plan_code != 'free' and getattr(subscription, 'status', None) == 'active' and getattr(subscription, 'is_valid', True):
            return plan_code

    # Product requirement: 14-day trial unlocks Pro-level features only.
    if getattr(tenant, 'is_trial_active', False):
        return 'pro'

    if plan:
        return normalize_plan_code(plan.code)
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
    plan = get_tenant_plan(user)
    plan_code = get_effective_plan_code(user)
    tenant = get_tenant(user)
    vip = is_vip_user(user)
    usage = get_current_usage(user)
    subscription = get_tenant_subscription(user)

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
        'is_vip': vip,
        'plan': {
            'code': plan_code,
            'name': 'VIP Access' if vip else (getattr(plan, 'name', None) if plan else plan_code.title()),
            'status': getattr(subscription, 'status', None),
            'current_billing_cycle': getattr(subscription, 'current_billing_cycle', 'monthly'),
            'current_period_start': getattr(subscription, 'current_period_start', None),
            'current_period_end': getattr(subscription, 'current_period_end', None),
            'pending_plan_code': getattr(getattr(subscription, 'pending_plan', None), 'code', None),
            'pending_plan_name': getattr(getattr(subscription, 'pending_plan', None), 'name', None),
            'pending_billing_cycle': getattr(subscription, 'pending_billing_cycle', None),
            'pending_plan_starts_at': getattr(subscription, 'pending_plan_starts_at', None),
            'prices': {
                'monthly': str(getattr(plan, 'monthly_price', Decimal('0.00')) if plan else Decimal('0.00')),
                'quarterly': str(getattr(plan, 'quarterly_price', Decimal('0.00')) if plan else Decimal('0.00')),
                'yearly': str(getattr(plan, 'yearly_price', Decimal('0.00')) if plan else Decimal('0.00')),
                'original_monthly': str(getattr(plan, 'original_monthly_price', Decimal('0.00')) if plan else Decimal('0.00')),
                'original_quarterly': str(getattr(plan, 'original_quarterly_price', Decimal('0.00')) if plan else Decimal('0.00')),
                'original_yearly': str(getattr(plan, 'original_yearly_price', Decimal('0.00')) if plan else Decimal('0.00')),
            },
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

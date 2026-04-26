from __future__ import annotations

from typing import Any, Callable

from django.core.cache import cache

CACHE_VERSION = 'v1'
CACHE_TTL_SHORT = 60
CACHE_TTL_MEDIUM = 300
CACHE_TTL_LONG = 3600


def _join_key_parts(*parts: Any) -> str:
    return ':'.join(str(part) for part in parts if part is not None and str(part) != '')


def global_cache_key(namespace: str, *parts: Any) -> str:
    return _join_key_parts('cenvora', namespace, CACHE_VERSION, *parts)


def tenant_cache_key(namespace: str, tenant_id: Any, *parts: Any) -> str:
    return _join_key_parts('cenvora', namespace, 'tenant', tenant_id, CACHE_VERSION, *parts)


def cache_get_or_set(key: str, timeout: int, builder: Callable[[], Any]) -> Any:
    cached_value = cache.get(key)
    if cached_value is not None:
        return cached_value

    value = builder()
    cache.set(key, value, timeout)
    return value


def cache_delete_many(*keys: str) -> None:
    cache.delete_many([key for key in keys if key])
"""
Cryptographic Perpetual License Key Issuance & Verification (subscription/licensing.py).
Uses HMAC-SHA256 signed payloads bound to tenant UUID and AMC expiry date.
"""
from datetime import date, timedelta
import hashlib
import hmac

from django.conf import settings


def _signing_secret() -> bytes:
    secret = getattr(settings, 'PERPETUAL_LICENSE_SECRET', None) or settings.SECRET_KEY
    return str(secret).encode('utf-8')


def sign_license_payload(tenant_id: str, issued_date: date, amc_valid_until: date) -> str:
    """
    Produce a deterministic, tamper-evident license key:
    CENV-<tenant_id_hex_12>-<issued_YYYYMMDD>-<amc_YYYYMMDD>-<hmac_16>
    """
    tid_short = str(tenant_id).replace('-', '')[:12].upper()
    iss_str = issued_date.strftime('%Y%m%d')
    amc_str = amc_valid_until.strftime('%Y%m%d')
    msg = f"{tenant_id}:{iss_str}:{amc_str}".encode('utf-8')
    sig = hmac.new(_signing_secret(), msg, hashlib.sha256).hexdigest()[:16].upper()
    return f"CENV-{tid_short}-{iss_str}-{amc_str}-{sig}"


def verify_license_key(license_key: str, tenant_id: str) -> tuple[bool, str]:
    """
    Verify a license key's format, tenant binding, and HMAC signature.
    Returns (is_valid, reason).
    """
    if not license_key or not isinstance(license_key, str):
        return False, "Missing license key"

    parts = license_key.strip().split('-')
    if len(parts) != 5 or parts[0] != 'CENV':
        return False, "Malformed license key"

    _, tid_short, iss_str, amc_str, sig = parts
    expected_tid_short = str(tenant_id).replace('-', '')[:12].upper()
    if tid_short != expected_tid_short:
        return False, "License key does not match tenant"

    msg = f"{tenant_id}:{iss_str}:{amc_str}".encode('utf-8')
    expected_sig = hmac.new(_signing_secret(), msg, hashlib.sha256).hexdigest()[:16].upper()
    if not hmac.compare_digest(sig, expected_sig):
        return False, "Invalid cryptographic signature"

    return True, "OK"


def issue_perpetual_license(tenant, amc_valid_until: date | None = None, grace_period_days: int = 30):
    """Create or renew a PerpetualLicense for `tenant`."""
    from .models import PerpetualLicense

    today = date.today()
    amc_until = amc_valid_until or (today + timedelta(days=365))
    key = sign_license_payload(str(tenant.id), today, amc_until)

    lic, _ = PerpetualLicense.objects.update_or_create(
        tenant=tenant,
        defaults={
            'license_key': key,
            'amc_valid_until': amc_until,
            'grace_period_days': grace_period_days,
            'is_revoked': False,
        },
    )
    return lic

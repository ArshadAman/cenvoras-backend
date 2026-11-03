"""
UAT P1 items 8 and 9 (Rudra CA): employee statutory identifiers.

Every value is upper-cased and trimmed before checking.
  PAN        ^[A-Z]{5}[0-9]{4}[A-Z]$, 4th character in PCHFATBLJG; an employee's PAN must have P
  IFSC       ^[A-Z]{4}0[A-Z0-9]{6}$
  UAN        ^[0-9]{12}$
  ESI IP no. ^[0-9]{10}$
  PF member  ^[A-Z]{5}[0-9]{17}$   (22 characters)
"""
import re

PAN_RE = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]$')
PAN_HOLDER_TYPES = 'PCHFATBLJG'
IFSC_RE = re.compile(r'^[A-Z]{4}0[A-Z0-9]{6}$')
UAN_RE = re.compile(r'^[0-9]{12}$')
ESI_IP_RE = re.compile(r'^[0-9]{10}$')
PF_MEMBER_RE = re.compile(r'^[A-Z]{5}[0-9]{17}$')


def clean(value):
    return re.sub(r'\s+', '', str(value or '')).upper()


def pan_error(value, individual=False):
    v = clean(value)
    if not PAN_RE.fullmatch(v):
        return 'Enter a valid PAN (5 letters, 4 digits, 1 letter, e.g. ABCPE1234F).'
    if v[3] not in PAN_HOLDER_TYPES:
        return "The PAN's 4th character must be one of P, C, H, F, A, T, B, L, J, G."
    if individual and v[3] != 'P':
        return "An employee's PAN must be an individual PAN (4th character 'P')."
    return None


def is_valid_pan(value, individual=False):
    return bool(value) and pan_error(value, individual) is None


def ifsc_error(value):
    return None if IFSC_RE.fullmatch(clean(value)) else (
        'Enter a valid IFSC (4 letters, then 0, then 6 letters or digits, e.g. SBIN0001234).')


def uan_error(value):
    return None if UAN_RE.fullmatch(clean(value)) else 'UAN must be exactly 12 digits.'


def esi_ip_error(value):
    return None if ESI_IP_RE.fullmatch(clean(value)) else 'ESI IP number must be exactly 10 digits.'


def pf_member_error(value):
    return None if PF_MEMBER_RE.fullmatch(clean(value)) else (
        'PF member ID must be 22 characters: 5 letters then 17 digits (e.g. MHBAN00123450000012345).')

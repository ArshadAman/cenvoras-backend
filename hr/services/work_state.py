"""Resolve employee work_state for PT slabs — never hardcode a state."""


def resolve_work_state(employee=None, tenant=None, work_state=None):
    """
    employee.work_state → normalize → else tenant.state → full GST state name.
    Returns '' only when both are blank (caller may still get ₹0 PT).
    """
    from billing.gst_utils import CODE_TO_STATE_NAME, normalize_state_code

    raw = (work_state or '').strip()
    if not raw and employee is not None:
        raw = (getattr(employee, 'work_state', None) or '').strip()
    if not raw:
        t = tenant
        if t is None and employee is not None:
            t = getattr(employee, 'tenant', None)
        if t is not None:
            raw = (getattr(t, 'state', None) or '').strip()

    if not raw:
        return ''

    code = normalize_state_code(raw)
    if code and code in CODE_TO_STATE_NAME:
        return CODE_TO_STATE_NAME[code]
    return raw


# Canonical PT work-state names: the 28 states and 8 UTs, same list as the frontend dropdown
# (SalaryBreakdownCalculator INDIAN_STATES) and the GST state names used by the PT slabs.
CANONICAL_WORK_STATES = (
    'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
    'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand',
    'Karnataka', 'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur',
    'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Punjab',
    'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura',
    'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
    'Andaman and Nicobar Islands', 'Chandigarh', 'Dadra and Nagar Haveli and Daman and Diu',
    'Delhi', 'Jammu and Kashmir', 'Ladakh', 'Lakshadweep', 'Puducherry',
)
_CANON_LOWER = {s.lower(): s for s in CANONICAL_WORK_STATES}
_ALIASES = {'daman and diu': 'Dadra and Nagar Haveli and Daman and Diu',
            'orissa': 'Odisha', 'pondicherry': 'Puducherry', 'nct of delhi': 'Delhi'}


def canonical_work_state(value):
    """Canonical state/UT name for value (case-insensitive; GST code/abbrev accepted), else None."""
    raw = ' '.join(str(value or '').split())
    if not raw:
        return None
    low = raw.lower()
    if low in _CANON_LOWER:
        return _CANON_LOWER[low]
    if low in _ALIASES:
        return _ALIASES[low]
    if len(raw) < 2:
        return None
    from billing.gst_utils import CODE_TO_STATE_NAME, normalize_state_code
    code = normalize_state_code(raw)
    name = CODE_TO_STATE_NAME.get(code) if code else None
    if name:
        return _CANON_LOWER.get(name.lower()) or _ALIASES.get(name.lower())
    return None

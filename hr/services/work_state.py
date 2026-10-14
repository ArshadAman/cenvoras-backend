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

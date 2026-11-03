"""
UAT P1 #10 (Rudra CA): Date-of-Joining correction.

Rules
- If any payroll exists for the employee from min(old, new DOJ) onwards, a normal
  edit of DOJ is blocked; only an admin-only DOJ correction with a reason is allowed.
- A correction never changes a run month. For each month that already has the
  employee's payslip (or a run that excluded them because of the old DOJ), the
  pay difference is computed on the SAME day-count basis as the original run
  (that payslip's total_working_days and the tenant's weekend rule) and summed into
  one line in the next open payroll:
    DOJ earlier → arrears (+);  DOJ later → recovery (−).
- The line is part of gross in the target month, so TDS (and PT/ESI) are
  re-projected by the normal engine on the corrected pay.
- If no payroll has been run yet, a plain edit is allowed.
"""
import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

CENT = Decimal('0.01')
# Run statuses after which a month is "closed" (never recomputed by a correction).
CLOSED_RUN_STATUSES = {'calculated', 'completed', 'pending_approval', 'approved', 'paid', 'locked', 'finalised'}


class DOJCorrectionError(ValueError):
    pass


def _month_key(d):
    return (d.year, d.month)


def _excluded_weekdays(settings):
    weekend_rule = getattr(settings, 'weekend_rule', 'sunday_only') if settings else 'sunday_only'
    lop_rule = getattr(settings, 'lop_calculation_rule', 'working_days') if settings else 'working_days'
    if weekend_rule == 'sat_sun' or lop_rule in ('working_days_5', 'exclude_sat_sun', '5_day_week'):
        return {6, 7}
    return {7}


def prejoin_days(doj, year, month, working_days, exclude_weekdays):
    """Days of the month not payable because of DOJ — mirrors get_attendance_breakdown."""
    start = datetime.date(year, month, 1)
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    if doj is None or doj <= start:
        return 0
    if doj > end:
        return int(working_days)
    n = sum(1 for d in range(1, doj.day)
            if datetime.date(year, month, d).isoweekday() not in exclude_weekdays)
    return min(n, int(working_days))


def month_delta(monthly_gross, working_days, old_doj, new_doj, year, month, exclude_weekdays):
    """Pay difference for one month (new − old). Decimal, half-up to paise."""
    w = int(working_days or 0)
    if w <= 0:
        return Decimal('0.00'), 0, 0
    old_payable = w - prejoin_days(old_doj, year, month, w, exclude_weekdays)
    new_payable = w - prejoin_days(new_doj, year, month, w, exclude_weekdays)
    delta = (Decimal(str(monthly_gross)) * Decimal(new_payable - old_payable) / Decimal(w)).quantize(
        CENT, rounding=ROUND_HALF_UP)
    return delta, old_payable, new_payable


def monthly_full_gross(employee, year, month):
    """Un-prorated monthly gross (earning components) of the assignment effective at month end."""
    from hr.models import EmployeeSalaryAssignment
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    a = EmployeeSalaryAssignment.objects.filter(employee=employee, effective_from__lte=end) \
        .select_related('salary_structure').order_by('-effective_from').first()
    if not a:
        return Decimal('0.00')
    types = {c.name: c.type for c in a.salary_structure.components.all()}
    total = Decimal('0.00')
    for name, val in (a.computed_components or {}).items():
        if str(name).startswith('_') or types.get(name, 'earning') == 'deduction':
            continue
        try:
            total += Decimal(str(val))
        except Exception:
            continue
    return total.quantize(CENT)


def payroll_months_from(employee, from_date):
    """Closed run months for the tenant from from_date's month, with this employee's payslip if any."""
    from hr.models import PayrollRun, Payslip
    runs = PayrollRun.objects.filter(tenant=employee.tenant, status__in=CLOSED_RUN_STATUSES)
    fk = _month_key(from_date)
    out = []
    for r in runs:
        if (r.year, r.month) < fk:
            continue
        slip = Payslip.objects.filter(payroll_run=r, employee=employee).first()
        out.append((r, slip))
    out.sort(key=lambda x: (x[0].year, x[0].month))
    return out


def employee_has_payroll_from(employee, from_date):
    from hr.models import Payslip
    fk = _month_key(from_date)
    for y, m in Payslip.objects.filter(employee=employee).values_list('payroll_run__year', 'payroll_run__month'):
        if (y, m) >= fk:
            return True
    return False


def next_open_month(tenant, after=None):
    from hr.models import PayrollRun
    closed = PayrollRun.objects.filter(tenant=tenant, status__in=CLOSED_RUN_STATUSES) \
        .order_by('-year', '-month').first()
    y, m = (closed.year, closed.month) if closed else (after or datetime.date.today()).timetuple()[:2]
    if after and (after.year, after.month) > (y, m):
        y, m = after.year, after.month
    m += 1
    if m == 13:
        y, m = y + 1, 1
    return y, m


def compute_correction(employee, new_doj):
    """Returns (amount, lines) without saving anything."""
    from hr.services.payroll_engine import get_hrms_settings, get_total_working_days
    old_doj = employee.date_of_joining
    settings = get_hrms_settings(employee.tenant)
    excl = _excluded_weekdays(settings)
    lines = []
    total = Decimal('0.00')
    for run, slip in payroll_months_from(employee, min(old_doj, new_doj)):
        if slip is not None:
            w = int(slip.total_working_days or 0)  # same basis as the original run
        else:
            w = get_total_working_days(
                run.month, run.year, settings.lop_calculation_rule,
                weekend_rule=getattr(settings, 'weekend_rule', 'sunday_only'),
                default_working_days=getattr(settings, 'default_working_days', None))
        gross = monthly_full_gross(employee, run.year, run.month)
        delta, old_p, new_p = month_delta(gross, w, old_doj, new_doj, run.year, run.month, excl)
        if delta == 0 and old_p == new_p:
            continue
        lines.append({'year': run.year, 'month': run.month, 'working_days': w,
                      'old_payable_days': old_p, 'new_payable_days': new_p,
                      'monthly_gross': str(gross), 'delta': str(delta),
                      'had_payslip': slip is not None})
        total += delta
    return total.quantize(CENT), lines


@transaction.atomic
def apply_doj_correction(employee, new_doj, reason, user):
    from hr.models import DOJCorrection
    if getattr(user, 'role', None) != 'admin':
        raise DOJCorrectionError('Only an admin can correct a date of joining.')
    reason = (reason or '').strip()
    if not reason:
        raise DOJCorrectionError('A reason is required for a DOJ correction.')
    if isinstance(new_doj, str):
        new_doj = datetime.date.fromisoformat(new_doj)
    old_doj = employee.date_of_joining
    if new_doj == old_doj:
        raise DOJCorrectionError('The new date of joining is the same as the current one.')
    amount, lines = compute_correction(employee, new_doj)
    ty, tm = next_open_month(employee.tenant)
    corr = DOJCorrection.objects.create(
        tenant=employee.tenant, employee=employee, old_doj=old_doj, new_doj=new_doj,
        reason=reason, corrected_by=user, amount=amount, breakdown=lines,
        target_month=tm, target_year=ty,
    )
    type(employee).objects.filter(pk=employee.pk).update(date_of_joining=new_doj)
    employee.date_of_joining = new_doj
    try:
        from audit_log.models import AuditLog
        AuditLog.objects.create(
            tenant=employee.tenant, user=user, user_email=getattr(user, 'email', '') or '',
            action='UPDATE', model_name='Employee', object_id=str(employee.pk),
            object_repr=str(employee)[:255],
            changes={'event': 'doj_correction', 'old': str(old_doj), 'new': str(new_doj),
                     'reason': reason, 'amount': str(amount), 'target': f'{tm:02d}/{ty}'},
        )
    except Exception:
        pass
    return corr


def pending_lines_for(employee, month, year):
    """Corrections to fold into this payslip: (label, amount) pairs; amount < 0 for recovery."""
    from hr.models import DOJCorrection
    out = []
    for c in DOJCorrection.objects.filter(employee=employee, target_month=month, target_year=year).exclude(amount=0):
        label = ('DOJ correction arrears' if c.amount > 0 else 'DOJ correction recovery') + \
            f' ({c.old_doj:%d-%m-%Y} → {c.new_doj:%d-%m-%Y})'
        out.append((label, Decimal(str(c.amount)).quantize(CENT), c))
    return out

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
- Rudra CA review (#205):
  (1) A recovery is a negative earning only for excess paid in the SAME FY as the
      run; excess paid in an earlier FY is a post-tax deduction (flagged). Taxable
      gross is floored at 0; any excess recovery carries into the next run.
  (2) PF: the PF-wage part (basic + DA) of arrears goes into that run's PF, capped at
      the ₹15k ceiling of the month the arrears relate to, kept separately as arrears
      (Payslip.arrear_* fields, for the ECR arrears columns). Locked months untouched.
  (3) A recovery carries the warning RECOVERY_PF_WARNING.
  (4) ESI: arrears count in the month paid; flagged ESIC_FLAG.
  PT on arrears is slabbed against the month they relate to (compute_pt_arrears).
- If no payroll has been run yet, a plain edit is allowed.
"""
import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

CENT = Decimal('0.01')
# Run statuses after which a month is "closed" (never recomputed by a correction).
CLOSED_RUN_STATUSES = {'calculated', 'completed', 'pending_approval', 'approved', 'paid', 'locked', 'finalised'}


RECOVERY_PF_WARNING = 'Excess PF already remitted must be corrected on the EPFO portal.'
ESIC_FLAG = 'ESIC treatment to verify'
PRIOR_FY_FLAG = 'Recovery of salary paid in an earlier FY: post-tax deduction (not a negative earning)'


def fy_start(year, month):
    return year if month >= 4 else year - 1


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


def _assignment(employee, year, month):
    from hr.models import EmployeeSalaryAssignment
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return EmployeeSalaryAssignment.objects.filter(employee=employee, effective_from__lte=end) \
        .select_related('salary_structure').order_by('-effective_from').first()


def monthly_full_gross(employee, year, month):
    """Un-prorated monthly gross (earning components) of the assignment effective at month end."""
    a = _assignment(employee, year, month)
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


PF_WAGE_NAMES = {'basic', 'da', 'dearness allowance', 'dearness_allowance'}


def monthly_pf_wage(employee, year, month):
    """Un-prorated PF wage (basic + DA) of the assignment effective at month end."""
    a = _assignment(employee, year, month)
    if not a:
        return Decimal('0.00')
    basic_names = {c.name for c in a.salary_structure.components.all() if getattr(c, 'is_basic', False)}
    comps = a.computed_components or {}
    total = Decimal('0.00')
    found = False
    for name, val in comps.items():
        if name in basic_names or str(name).strip().lower() in PF_WAGE_NAMES:
            try:
                total += Decimal(str(val))
                found = True
            except Exception:
                pass
    if not found:  # same fallback as the engine: 50% of CTC
        total = (Decimal(str(a.monthly_ctc or 0)) * Decimal('0.50'))
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
    emp_rate = Decimal(str(getattr(settings, 'pf_employee_rate', 12) or 12)) / Decimal('100')
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
        pf_full = monthly_pf_wage(employee, run.year, run.month)
        pf_delta, _, _ = month_delta(pf_full, w, old_doj, new_doj, run.year, run.month, excl)
        paid_pf_wage = (Decimal(str(slip.employee_pf or 0)) / emp_rate).quantize(CENT) \
            if (slip is not None and emp_rate > 0) else Decimal('0.00')
        lines.append({'year': run.year, 'month': run.month, 'working_days': w,
                      'old_payable_days': old_p, 'new_payable_days': new_p,
                      'monthly_gross': str(gross), 'delta': str(delta),
                      'pf_wage_delta': str(pf_delta), 'paid_pf_wage': str(paid_pf_wage),
                      'paid_gross': str(slip.gross_salary if slip is not None else Decimal('0.00')),
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


def _pf_ceiling(settings):
    if settings is not None and not bool(getattr(settings, 'pf_apply_ceiling', True)):
        return None
    return Decimal(str(getattr(settings, 'pf_wage_ceiling', None) or 15000))


def arrears_pf(lines, settings):
    """PF on the arrears' PF wage, capped per RELATED month at that month's ceiling."""
    from hr.services.payroll_engine import compute_pf
    ceiling = _pf_ceiling(settings)
    out = {'wages': Decimal('0.00'), 'employee_pf': Decimal('0.00'), 'employer_pf': Decimal('0.00'),
           'employer_epf': Decimal('0.00'), 'employer_eps': Decimal('0.00')}
    for ln in lines:
        d = Decimal(str(ln.get('pf_wage_delta', '0')))
        if d <= 0:
            continue
        paid = Decimal(str(ln.get('paid_pf_wage', '0')))
        wage = d if ceiling is None else max(Decimal('0.00'), min(d, ceiling - paid))
        if wage <= 0:
            continue
        before = compute_pf(paid, settings)
        after = compute_pf(paid + wage, settings)
        out['wages'] += wage
        for k in ('employee_pf', 'employer_pf', 'employer_epf', 'employer_eps'):
            out[k] += Decimal(str(after[k])) - Decimal(str(before[k]))
    return {k: v.quantize(CENT) for k, v in out.items()}


def pending_for_payslip(employee, month, year, settings=None):
    """
    Everything a DOJ correction contributes to the (month, year) payslip:
      earning_lines  [(label, amount)]  arrears (+) and same-FY recovery (−)
      deduction_lines [(label, amount)] earlier-FY recovery (post-tax, positive amounts)
      pf, pt_arrears, warnings, corrections
    """
    from hr.models import DOJCorrection
    from hr.services.payroll_engine import compute_pt_arrears
    run_fy = fy_start(year, month)
    res = {'earning_lines': [], 'deduction_lines': [], 'pt_arrears': Decimal('0.00'),
           'pf': arrears_pf([], settings), 'warnings': [], 'corrections': []}
    pf_lines = []
    for c in DOJCorrection.objects.filter(employee=employee, target_month=month, target_year=year).exclude(amount=0):
        res['corrections'].append(c)
        tag = f'({c.old_doj:%d-%m-%Y} → {c.new_doj:%d-%m-%Y})'
        if c.carried_from_id:
            tag += ' carried forward'
        same_fy = prior_fy = arrears = Decimal('0.00')
        for ln in c.breakdown or []:
            d = Decimal(str(ln['delta']))
            if d >= 0:
                arrears += d
                pf_lines.append(ln)
                if not ln.get('carry'):
                    paid = Decimal(str(ln.get('paid_gross', '0')))
                    res['pt_arrears'] += compute_pt_arrears(employee, ln['month'], ln['year'], paid, paid + d,
                                                            tenant=employee.tenant)
            elif fy_start(int(ln['year']), int(ln['month'])) == run_fy:
                same_fy += d
            else:
                prior_fy += d
                res.setdefault('prior_fy_month', (int(ln['month']), int(ln['year'])))
        if arrears:
            res['earning_lines'].append((f'DOJ correction arrears {tag} [{ESIC_FLAG}]', arrears.quantize(CENT)))
        if same_fy:
            res['earning_lines'].append((f'DOJ correction recovery {tag}', same_fy.quantize(CENT)))
        if prior_fy:
            res['deduction_lines'].append((f'DOJ correction recovery, earlier FY {tag}', (-prior_fy).quantize(CENT)))
            res['warnings'].append(PRIOR_FY_FLAG)
        if same_fy or prior_fy:
            res['warnings'].append(RECOVERY_PF_WARNING)
        if arrears:
            res['warnings'].append(f'Arrears: {ESIC_FLAG}')
    res['pf'] = arrears_pf(pf_lines, settings)
    res['warnings'] = list(dict.fromkeys(res['warnings']))
    return res


def carry_excess_recovery(corrections, excess, month, year):
    """Taxable gross was floored at 0: carry the unrecovered same-FY amount into the next run (idempotent)."""
    from hr.models import DOJCorrection
    if not corrections:
        return None
    parent = corrections[0]
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    DOJCorrection.objects.filter(carried_from=parent, target_month=nm, target_year=ny).exclude(
        reason__startswith=PRIOR_FY_CARRY_REASON).delete()
    if excess <= 0:
        return None
    return DOJCorrection.objects.create(
        tenant=parent.tenant, employee=parent.employee, old_doj=parent.old_doj, new_doj=parent.new_doj,
        reason=f'Carried forward: excess recovery not covered by {month:02d}/{year} pay',
        corrected_by=parent.corrected_by, amount=-excess, carried_from=parent,
        breakdown=[{'year': year, 'month': month, 'delta': str(-excess), 'carry': True}],
        target_month=nm, target_year=ny,
    )


PRIOR_FY_CARRY_REASON = 'Carried forward (earlier-FY recovery)'
# VERIFY limit/applicability: Code on Wages s.18 caps total deductions at 50% of wages.
DEFAULT_RECOVERY_CAP_PERCENT = Decimal('50.00')


def cap_prior_fy_recovery(deduction_lines, wages, available, cap_percent=None):
    """
    Earlier-FY recovery is a post-tax deduction. Cap it at cap_percent of the month's wages and at
    the pay left after all other deductions, so net pay never goes below 0.
    Returns (applied_lines, recovered, balance) with balance carried to the next run.
    """
    pct = DEFAULT_RECOVERY_CAP_PERCENT if cap_percent is None else Decimal(str(cap_percent))
    total = sum((a for _l, a in deduction_lines), Decimal('0.00'))
    cap = (max(Decimal('0.00'), Decimal(str(wages))) * pct / Decimal('100')).quantize(CENT)
    allowed = max(Decimal('0.00'), min(cap, Decimal(str(available)).quantize(CENT)))
    left = min(total, allowed)
    recovered = left
    applied = []
    for label, amt in deduction_lines:
        take = min(amt, left)
        left -= take
        if take > 0:
            applied.append((label, take.quantize(CENT)))
    return applied, recovered.quantize(CENT), (total - recovered).quantize(CENT)


def carry_prior_fy_recovery(corrections, balance, month, year, related_month, related_year):
    """Carry the uncovered earlier-FY recovery to the next run, keeping it earlier-FY (idempotent)."""
    from hr.models import DOJCorrection
    if not corrections:
        return None
    parent = corrections[0]
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    DOJCorrection.objects.filter(carried_from=parent, target_month=nm, target_year=ny,
                                 reason__startswith=PRIOR_FY_CARRY_REASON).delete()
    if balance <= 0:
        return None
    return DOJCorrection.objects.create(
        tenant=parent.tenant, employee=parent.employee, old_doj=parent.old_doj, new_doj=parent.new_doj,
        reason=f'{PRIOR_FY_CARRY_REASON}: balance after the {month:02d}/{year} cap',
        corrected_by=parent.corrected_by, amount=-balance, carried_from=parent,
        breakdown=[{'year': related_year, 'month': related_month, 'delta': str(-balance), 'carry': True}],
        target_month=nm, target_year=ny,
    )


def pending_lines_for(employee, month, year):
    """Back-compat: earning lines only."""
    return [(l, a, None) for l, a in pending_for_payslip(employee, month, year)['earning_lines']]

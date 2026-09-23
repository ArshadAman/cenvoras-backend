import datetime
import calendar
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from hr.models import (
    PayrollRun, Employee, Payslip, AttendanceRecord,
    EmployeeSalaryAssignment, ProfessionalTaxSlab,
    OvertimeRecord, EmployeeAdvanceLoan, HRMSSettings,
    EmployeeAllowanceBonus, LeaveApplication
)
from .exceptions_service import scan_payroll_exceptions
from .tds_service import compute_dynamic_tds, compute_tax_on_income


def get_hrms_settings(tenant):
    """Retrieve or create default tenant HRMS configuration."""
    settings, _ = HRMSSettings.objects.get_or_create(tenant=tenant)
    return settings


def apply_rounding(amount, rounding_rule='nearest_one'):
    """
    Rounds an amount according to tenant HRMSSettings.salary_rounding:
    - 'nearest_one': Rounds to the nearest ₹1 (e.g., 23800.40 -> 23800.00, 23800.50 -> 23801.00)
    - 'nearest_ten': Rounds to the nearest ₹10 (e.g., 23804.00 -> 23800.00, 23806.00 -> 23810.00)
    - 'exact': Keeps exact 2 decimal places.
    """
    amt = Decimal(str(amount))
    if rounding_rule == 'nearest_ten':
        val = round(float(amt) / 10.0) * 10
        return Decimal(str(f"{val:.2f}"))
    elif rounding_rule == 'exact':
        return amt.quantize(Decimal('0.01'))
    else:  # default 'nearest_one'
        val = round(float(amt))
        return Decimal(str(f"{val:.2f}"))


def get_total_working_days(month, year, lop_rule='working_days', weekend_rule='sunday_only'):
    """Calculate total expected working days in the month according to the configured rule."""
    days_in_month = calendar.monthrange(year, month)[1]
    if lop_rule == 'calendar_days':
        return days_in_month
    elif lop_rule == 'fixed_30':
        return 30

    exclude_weekdays = {7}  # Sunday
    if weekend_rule == 'sat_sun' or lop_rule in ['working_days_5', 'exclude_sat_sun', '5_day_week']:
        exclude_weekdays = {6, 7}  # Saturday & Sunday

    working_days = 0
    for day in range(1, days_in_month + 1):
        if datetime.date(year, month, day).isoweekday() not in exclude_weekdays:
            working_days += 1
    return working_days or days_in_month


def get_attendance_breakdown(employee, month, year, total_working_days, hrms_settings=None):
    """
    Computes effective present days, paid leave days, absent days, and LOP days.
    Guarantees:
    1. Unlogged days between joining date and month-end default to present (no partial-log penalty).
    2. Mid-month joiners count pre-joining days as LOP while preserving post-joining absences.
    3. Unpaid leaves and quota-exhausted leaves are tracked as LOP, not paid days.
    4. Respects tenant weekend settings (Sunday only vs. Saturday & Sunday).
    """
    if hrms_settings is None and employee:
        hrms_settings = get_hrms_settings(employee.tenant)

    weekend_rule = getattr(hrms_settings, 'weekend_rule', 'sunday_only') if hrms_settings else 'sunday_only'
    lop_rule = getattr(hrms_settings, 'lop_calculation_rule', 'working_days') if hrms_settings else 'working_days'

    exclude_weekdays = {7}
    if weekend_rule == 'sat_sun' or lop_rule in ['working_days_5', 'exclude_sat_sun', '5_day_week']:
        exclude_weekdays = {6, 7}

    start_date = datetime.date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, days_in_month)

    doj = employee.date_of_joining if employee else None
    if isinstance(doj, str):
        try:
            doj = datetime.date.fromisoformat(doj)
        except (ValueError, TypeError):
            doj = start_date

    # Mid-month joining days before joining date
    days_before_joining = 0
    if doj and doj > start_date and doj <= end_date:
        for d in range(1, doj.day):
            if datetime.date(year, month, d).isoweekday() not in exclude_weekdays:
                days_before_joining += 1

    records = {
        r.date: r for r in AttendanceRecord.objects.filter(
            employee=employee,
            date__range=(start_date, end_date)
        )
    }

    # Pre-fetch approved unpaid/LWP leave dates for accurate LOP detection
    approved_leaves = LeaveApplication.objects.filter(
        employee=employee,
        status='approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    ).select_related('leave_type')

    unpaid_leave_dates = set()
    for app in approved_leaves:
        if not app.leave_type.is_paid or app.lwp_days > 0:
            cur = max(app.start_date, start_date)
            app_end = min(app.end_date, end_date)
            while cur <= app_end:
                if cur.isoweekday() not in exclude_weekdays:
                    unpaid_leave_dates.add(cur)
                cur += datetime.timedelta(days=1)

    absent_days = Decimal('0.0')
    paid_leave_days = Decimal('0.0')
    logged_lop_days = Decimal('0.0')
    absent_dates_list = []
    unpaid_leave_dates_list = []

    for d in range(1, days_in_month + 1):
        cur_date = datetime.date(year, month, d)
        if cur_date.isoweekday() in exclude_weekdays:
            continue  # Weekend

        # If before joining, it's counted in days_before_joining
        if doj and cur_date < doj:
            continue

        rec = records.get(cur_date)
        if rec:
            if rec.status == 'absent':
                absent_days += Decimal('1.0')
                logged_lop_days += Decimal('1.0')
                absent_dates_list.append(str(cur_date))
            elif rec.status == 'half_day':
                absent_days += Decimal('0.5')
                logged_lop_days += Decimal('0.5')
                absent_dates_list.append(f"{cur_date} (half-day)")
            elif rec.status == 'leave':
                if cur_date in unpaid_leave_dates:
                    logged_lop_days += Decimal('1.0')
                    unpaid_leave_dates_list.append(str(cur_date))
                else:
                    paid_leave_days += Decimal('1.0')

    total_lop = Decimal(str(days_before_joining)) + logged_lop_days
    effective_present = max(Decimal('0.0'), Decimal(str(total_working_days)) - total_lop)

    return {
        'present_days': effective_present,
        'paid_leave_days': paid_leave_days,
        'absent_days': absent_days,
        'lop_days': total_lop,
        'days_before_joining': days_before_joining,
        'absent_dates': absent_dates_list,
        'unpaid_leave_dates': unpaid_leave_dates_list,
    }


def get_present_days(employee, month, year):
    """Backward-compatible helper returning effective present days."""
    total_working = get_total_working_days(month, year)
    breakdown = get_attendance_breakdown(employee, month, year, total_working)
    return breakdown['present_days']


def compute_overtime(employee, month, year):
    """Gathers approved overtime hours and total payout for the month."""
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])

    overtimes = OvertimeRecord.objects.filter(
        employee=employee,
        date__range=(start_date, end_date),
        status='approved'
    )

    total_hours = sum((o.hours for o in overtimes), Decimal('0.0'))
    total_amount = sum((o.amount for o in overtimes), Decimal('0.0'))

    return total_hours, total_amount


def compute_pf(basic_salary, hrms_settings=None):
    """
    Computes Employee PF and Employer PF contributions.
    Supports statutory ₹15,000 wage ceiling and ₹1,250 statutory EPS cap.
    """
    basic = Decimal(str(basic_salary))
    if hrms_settings and hrms_settings.pf_apply_ceiling:
        pf_wage = min(basic, Decimal(str(hrms_settings.pf_wage_ceiling)))
    else:
        pf_wage = basic

    emp_rate = Decimal(str(hrms_settings.pf_employee_rate if hrms_settings else 12.0)) / Decimal('100')
    empr_rate = Decimal(str(hrms_settings.pf_employer_rate if hrms_settings else 12.0)) / Decimal('100')

    emp_pf = (pf_wage * emp_rate).quantize(Decimal('0.01'))
    employer_pf = (pf_wage * empr_rate).quantize(Decimal('0.01'))

    # Statutory EPFO EPS Cap: 8.33% capped at ₹1,250 (which is 8.33% of ₹15,000 ceiling).
    # The remainder of the 12% employer contribution goes to EPF.
    eps_wage_ceiling = Decimal('15000.00')
    eps_wage = min(pf_wage, eps_wage_ceiling)
    if eps_wage >= eps_wage_ceiling:
        employer_eps = Decimal('1250.00')
    else:
        employer_eps = min(Decimal(str(round(float(eps_wage) * (8.33 / 100.0), 2))), Decimal('1250.00'))
    employer_epf = max(Decimal('0.00'), employer_pf - employer_eps)

    return {
        'employee_pf': emp_pf,
        'employer_pf': employer_pf,
        'employer_epf': employer_epf,
        'employer_eps': employer_eps
    }


def compute_esi(gross_salary, hrms_settings=None):
    """
    Computes Employee and Employer ESI.
    Applies only if gross monthly salary is <= ₹21,000 (statutory threshold).
    """
    gross = Decimal(str(gross_salary))
    ceiling = Decimal(str(hrms_settings.esi_wage_ceiling if hrms_settings else 21000.0))

    if gross <= ceiling:
        emp_rate = Decimal(str(hrms_settings.esi_employee_rate if hrms_settings else 0.75)) / Decimal('100')
        empr_rate = Decimal(str(hrms_settings.esi_employer_rate if hrms_settings else 3.25)) / Decimal('100')
        emp_esi = (gross * emp_rate).quantize(Decimal('0.01'))
        employer_esi = (gross * empr_rate).quantize(Decimal('0.01'))
    else:
        emp_esi = Decimal('0.00')
        employer_esi = Decimal('0.00')

    return {
        'employee_esi': emp_esi,
        'employer_esi': employer_esi
    }


def compute_tds(gross_salary, regime='new'):
    """
    Computes monthly Tax Deducted at Source based on projected annual gross and tax regime.
    Defaults to New Regime (Sec 115BAC) with Standard Deduction (Rs. 75,000) and Sec 87A rebate.
    """
    annual_gross = Decimal(str(gross_salary)) * Decimal('12')
    std_ded = Decimal('75000.00') if regime == 'new' else Decimal('50000.00')
    taxable_income = max(Decimal('0.00'), annual_gross - std_ded)
    _, _, _, _, total_annual_tax = compute_tax_on_income(taxable_income, regime=regime)
    monthly_tax = total_annual_tax / Decimal('12')
    return monthly_tax.quantize(Decimal('0.01'))


def compute_pt(gross_salary, work_state):
    """Calculates state-wise Professional Tax using ProfessionalTaxSlab."""
    gross = Decimal(str(gross_salary))
    slab = ProfessionalTaxSlab.objects.filter(
        state_name__iexact=work_state or '',
        lower_bound__lte=gross
    ).filter(
        Q(upper_bound__isnull=True) | Q(upper_bound__gte=gross)
    ).first()

    if slab:
        return Decimal(str(slab.pt_amount))
    return Decimal('0.00')


def compute_gross(employee, month, year):
    """
    Computes prorated gross salary, differentiating earning components from deduction components.
    Ensures that components defined with type='earning' form the gross salary,
    while type='deduction' components are categorized as deductions.
    """
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])

    assignment = EmployeeSalaryAssignment.objects.filter(
        employee=employee,
        effective_from__lte=end_date
    ).order_by('-effective_from').first()

    if not assignment:
        return Decimal('0.0'), None, Decimal('0.0'), {}, {}

    settings = get_hrms_settings(employee.tenant)
    working_days = Decimal(str(get_total_working_days(month, year, settings.lop_calculation_rule)))

    if working_days == Decimal('0.0'):
        return Decimal('0.0'), assignment, Decimal('0.0'), {}, {}

    breakdown = get_attendance_breakdown(employee, month, year, int(working_days))
    present_days = breakdown['present_days']

    proration_factor = present_days / working_days

    # Separate earnings from deductions based on SalaryStructure component types
    structure_components = {c.name: c for c in assignment.salary_structure.components.all()}
    
    total_monthly_gross = Decimal('0.0')
    earnings_map = {}
    custom_deductions_map = {}

    for comp_name, value_str in assignment.computed_components.items():
        comp_obj = structure_components.get(comp_name)
        comp_type = comp_obj.type if comp_obj else 'earning'
        val = Decimal(str(value_str))

        if comp_type == 'deduction':
            # Custom deduction defined in salary structure (e.g. Canteen, Transport)
            prorated_val = (val * proration_factor).quantize(Decimal('0.01'))
            custom_deductions_map[comp_name] = prorated_val
        else:
            total_monthly_gross += val
            prorated_val = (val * proration_factor).quantize(Decimal('0.01'))
            earnings_map[comp_name] = prorated_val

    # Balancing Component: If total earnings < monthly_ctc and no component covers it,
    # allocate remainder to Special Allowance so employee receives full assigned compensation.
    monthly_ctc = Decimal(str(assignment.monthly_ctc))
    if total_monthly_gross < monthly_ctc and 'Special Allowance' not in earnings_map:
        remainder = monthly_ctc - total_monthly_gross
        total_monthly_gross = monthly_ctc
        earnings_map['Special Allowance'] = (remainder * proration_factor).quantize(Decimal('0.01'))

    prorated_gross = (total_monthly_gross * proration_factor).quantize(Decimal('0.01'))

    return prorated_gross, assignment, proration_factor, earnings_map, custom_deductions_map


def compute_advances_and_loans(employee, gross_available, allow_negative=False):
    """
    Calculates monthly installment recovery for active advances and loans.
    Deduction is strictly capped at the outstanding balance.
    Returns: advance_deduction, loan_deduction, recovery_details list
    """
    active_loans = EmployeeAdvanceLoan.objects.filter(
        employee=employee,
        status='active',
        outstanding_balance__gt=0
    ).order_by('disbursement_date')

    advance_deduction = Decimal('0.00')
    loan_deduction = Decimal('0.00')
    available = Decimal(str(gross_available))
    recovery_details = []

    for loan in active_loans:
        if not allow_negative and available <= 0:
            break

        installment = min(Decimal(str(loan.monthly_installment)), Decimal(str(loan.outstanding_balance)))
        if not allow_negative:
            installment = min(installment, available)

        if loan.record_type == 'advance':
            advance_deduction += installment
        else:
            loan_deduction += installment

        available -= installment
        recovery_details.append({
            'loan_id': str(loan.id),
            'record_type': loan.record_type,
            'installment': installment,
            'remaining_after': loan.outstanding_balance - installment,
            'reason': f"{loan.get_record_type_display()} EMI recovery (Balance after: ₹{loan.outstanding_balance - installment})"
        })

    return advance_deduction, loan_deduction, recovery_details


def compute_payslip_for_employee(employee, payroll_run):
    """
    Computes a complete, transparent payslip for an employee within a payroll run.
    Stores itemized earnings, deductions, and user-facing deduction reasons.
    """
    month = payroll_run.month
    year = payroll_run.year
    tenant = payroll_run.tenant
    settings = get_hrms_settings(tenant)

    working_days = get_total_working_days(month, year, settings.lop_calculation_rule)
    breakdown = get_attendance_breakdown(employee, month, year, working_days)

    prorated_gross, assignment, proration_factor, earnings_map, custom_deductions = compute_gross(employee, month, year)

    if not assignment:
        return None

    # Overtime
    ot_hours, ot_amount = compute_overtime(employee, month, year)

    # Allowances and Bonuses for this month/year (or recurring)
    allowances_bonuses = EmployeeAllowanceBonus.objects.filter(
        employee=employee,
        status='approved'
    ).filter(
        Q(is_recurring=True) | Q(effective_date__year=year, effective_date__month=month)
    )
    total_ab_amount = sum((Decimal(str(ab.amount)) for ab in allowances_bonuses), Decimal('0.00'))

    gross_with_earnings = (prorated_gross + ot_amount + total_ab_amount).quantize(Decimal('0.01'))

    # Basic component lookup for PF
    basic_comp = assignment.salary_structure.components.filter(is_basic=True).first()
    if basic_comp:
        base_basic_str = assignment.computed_components.get(basic_comp.name, '0.0')
        basic_salary = (Decimal(str(base_basic_str)) * proration_factor).quantize(Decimal('0.01'))
    else:
        basic_salary = Decimal('0.0')

    # Statutory Calculations
    pf_details = compute_pf(basic_salary, settings)
    esi_details = compute_esi(gross_with_earnings, settings)
    tds_data = compute_dynamic_tds(employee, gross_with_earnings, month, year)
    tds_val = tds_data['monthly_tds']
    pt_val = compute_pt(gross_with_earnings, employee.work_state)

    # Custom deductions total
    custom_deductions_total = sum(custom_deductions.values(), Decimal('0.00'))

    # Statutory Deductions subtotal
    statutory_deductions = (
        pf_details['employee_pf'] +
        esi_details['employee_esi'] +
        tds_val +
        pt_val +
        custom_deductions_total
    )

    available_for_loans = max(Decimal('0.00'), gross_with_earnings - statutory_deductions)
    advance_rec, loan_rec, loan_recovery_details = compute_advances_and_loans(
        employee, available_for_loans, settings.allow_negative_salary
    )

    raw_total_deductions = (statutory_deductions + advance_rec + loan_rec).quantize(Decimal('0.01'))
    raw_net_salary = (gross_with_earnings - raw_total_deductions).quantize(Decimal('0.01'))

    # Apply tenant salary rounding rule
    rounding_rule = settings.salary_rounding or 'nearest_one'
    net_salary = apply_rounding(raw_net_salary, rounding_rule)
    total_deductions = (gross_with_earnings - net_salary).quantize(Decimal('0.01'))

    # Employer Contributions
    employer_total = (pf_details['employer_pf'] + esi_details['employer_esi']).quantize(Decimal('0.01'))

    # Itemized Breakdown
    earnings = {}
    for cname, cval in earnings_map.items():
        earnings[cname] = str(cval)

    if ot_amount > 0:
        earnings['Overtime'] = str(ot_amount)

    for ab in allowances_bonuses:
        key_label = f"{ab.get_record_type_display()}: {ab.title}"
        earnings[key_label] = str(Decimal(str(ab.amount)).quantize(Decimal('0.01')))

    deductions = {}
    deduction_reasons = {}

    # 1. Loss of Pay (LOP) Reason
    if breakdown['lop_days'] > 0:
        lop_reasons = []
        if breakdown['days_before_joining'] > 0:
            lop_reasons.append(f"{breakdown['days_before_joining']} day(s) before joining ({employee.date_of_joining})")
        if breakdown['absent_dates']:
            lop_reasons.append(f"Absences on: {', '.join(breakdown['absent_dates'][:4])}")
        if breakdown['unpaid_leave_dates']:
            lop_reasons.append(f"Unpaid leave on: {', '.join(breakdown['unpaid_leave_dates'][:4])}")
        
        reason_text = f"Prorated salary based on {breakdown['present_days']} present days of {working_days} working days. " + "; ".join(lop_reasons)
        deduction_reasons['Loss of Pay (LOP)'] = {
            'days': str(breakdown['lop_days']),
            'reason': reason_text
        }

    # 2. Provident Fund (PF)
    if pf_details['employee_pf'] > 0:
        deductions['PF'] = str(pf_details['employee_pf'])
        deduction_reasons['PF'] = {
            'amount': str(pf_details['employee_pf']),
            'reason': f"Employee statutory 12% contribution on Basic salary ₹{basic_salary}"
        }

    # 3. Employee State Insurance (ESI)
    if esi_details['employee_esi'] > 0:
        deductions['ESI'] = str(esi_details['employee_esi'])
        deduction_reasons['ESI'] = {
            'amount': str(esi_details['employee_esi']),
            'reason': f"Employee statutory 0.75% contribution on gross ₹{gross_with_earnings} (≤ ₹21,000 threshold)"
        }

    # 4. Tax Deducted at Source (TDS under Section 192)
    if tds_val > 0:
        deductions['TDS'] = str(tds_val)
        deduction_reasons['TDS'] = {
            'amount': str(tds_val),
            'reason': tds_data['reason']
        }

    # 5. Professional Tax (PT)
    if pt_val > 0:
        deductions['PT'] = str(pt_val)
        deduction_reasons['PT'] = {
            'amount': str(pt_val),
            'reason': f"State statutory Professional Tax slab deduction for {employee.work_state or 'registered state'}"
        }

    # 6. Custom Structure Deductions
    for cname, cval in custom_deductions.items():
        deductions[cname] = str(cval)
        deduction_reasons[cname] = {
            'amount': str(cval),
            'reason': f"Recurring salary deduction component defined in {assignment.salary_structure.name}"
        }

    # 7. Salary Advance Recovery
    if advance_rec > 0:
        deductions['Salary Advance Recovery'] = str(advance_rec)
        adv_details = [d['reason'] for d in loan_recovery_details if d['record_type'] == 'advance']
        deduction_reasons['Salary Advance Recovery'] = {
            'amount': str(advance_rec),
            'reason': "; ".join(adv_details) or "Monthly salary advance recovery installment"
        }

    # 8. Loan Recovery
    if loan_rec > 0:
        deductions['Loan Recovery'] = str(loan_rec)
        ln_details = [d['reason'] for d in loan_recovery_details if d['record_type'] == 'loan']
        deduction_reasons['Loan Recovery'] = {
            'amount': str(loan_rec),
            'reason': "; ".join(ln_details) or "Monthly personal loan recovery installment"
        }

    payslip = Payslip(
        tenant=tenant,
        payroll_run=payroll_run,
        employee=employee,
        present_days=breakdown['present_days'],
        total_working_days=working_days,
        absent_days=breakdown['absent_days'],
        paid_leave_days=breakdown['paid_leave_days'],
        lop_days=breakdown['lop_days'],
        overtime_hours=ot_hours,
        overtime_amount=ot_amount,
        gross_salary=gross_with_earnings,
        earnings=earnings,
        deductions=deductions,
        deduction_reasons=deduction_reasons,
        employee_pf=pf_details['employee_pf'],
        employee_esi=esi_details['employee_esi'],
        tds=tds_val,
        professional_tax=pt_val,
        advance_recovery=advance_rec,
        loan_recovery=loan_rec,
        total_deductions=total_deductions,
        employer_pf=pf_details['employer_pf'],
        employer_epf=pf_details['employer_epf'],
        employer_eps=pf_details['employer_eps'],
        employer_esi=esi_details['employer_esi'],
        employer_total_contribution=employer_total,
        net_salary=net_salary,
    )
    return payslip


def run_payroll(payroll_run_id):
    """
    Executes the monthly payroll computation for all eligible employees.
    Atomic, deterministic, and traceable.
    """
    try:
        run = PayrollRun.objects.get(id=payroll_run_id)
    except PayrollRun.DoesNotExist:
        return

    with transaction.atomic():
        Payslip.objects.filter(payroll_run=run).delete()

        # Eligible employees: joined on or before month-end
        # Include active/probation employees, plus any employee who logged attendance in this month
        days_in_month = calendar.monthrange(run.year, run.month)[1]
        start_date = datetime.date(run.year, run.month, 1)
        end_date = datetime.date(run.year, run.month, days_in_month)

        employees = Employee.objects.filter(
            tenant=run.tenant,
            date_of_joining__lte=end_date,
        ).filter(
            Q(status__in=['active', 'probation', 'notice_period']) |
            Q(attendance_records__date__range=(start_date, end_date))
        ).distinct()

        payslips_to_create = []
        total_gross = Decimal('0.00')
        total_deductions = Decimal('0.00')
        total_net = Decimal('0.00')
        total_empr_contributions = Decimal('0.00')

        for emp in employees:
            payslip = compute_payslip_for_employee(emp, run)
            if payslip:
                payslips_to_create.append(payslip)
                total_gross += payslip.gross_salary
                total_deductions += payslip.total_deductions
                total_net += payslip.net_salary
                total_empr_contributions += payslip.employer_total_contribution

        if payslips_to_create:
            Payslip.objects.bulk_create(payslips_to_create)

        run.total_gross = total_gross
        run.total_deductions = total_deductions
        run.total_net = total_net
        run.total_employer_contributions = total_empr_contributions
        run.status = 'calculated'
        run.save(update_fields=[
            'total_gross', 'total_deductions', 'total_net',
            'total_employer_contributions', 'status'
        ])

        # Scan for exceptions (missing bank details, PAN, unapproved overtime/leave, negative net pay)
        scan_payroll_exceptions(run.id)

import datetime
import calendar
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from hr.models import (
    PayrollRun, Employee, Payslip, AttendanceRecord,
    EmployeeSalaryAssignment, ProfessionalTaxSlab,
    OvertimeRecord, EmployeeAdvanceLoan, HRMSSettings
)
from .exceptions_service import scan_payroll_exceptions


def get_hrms_settings(tenant):
    """Retrieve or create default tenant HRMS configuration."""
    settings, _ = HRMSSettings.objects.get_or_create(tenant=tenant)
    return settings


def get_total_working_days(month, year, lop_rule='working_days'):
    """Calculate total expected working days in the month according to the configured rule."""
    days_in_month = calendar.monthrange(year, month)[1]
    if lop_rule == 'calendar_days':
        return days_in_month
    elif lop_rule == 'fixed_30':
        return 30
    else:  # 'working_days' (Mon-Sat, excluding Sundays)
        working_days = 0
        for day in range(1, days_in_month + 1):
            if datetime.date(year, month, day).isoweekday() != 7:  # Sunday = 7
                working_days += 1
        return working_days or days_in_month


def get_attendance_breakdown(employee, month, year, total_working_days):
    """
    Computes effective present days, paid leave days, absent days, and LOP days.
    """
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])

    # Account for mid-month joinee
    doj = employee.date_of_joining
    if isinstance(doj, str):
        try:
            doj = datetime.date.fromisoformat(doj)
        except (ValueError, TypeError):
            doj = start_date
    effective_start = max(start_date, doj) if doj else start_date

    records = AttendanceRecord.objects.filter(
        employee=employee,
        date__range=(start_date, end_date)
    )

    present_days = Decimal('0.0')
    paid_leave_days = Decimal('0.0')
    absent_days = Decimal('0.0')
    lop_days = Decimal('0.0')

    has_records = records.exists()

    if has_records:
        for rec in records:
            if rec.status == 'present':
                present_days += Decimal('1.0')
            elif rec.status == 'half_day':
                present_days += Decimal('0.5')
                lop_days += Decimal('0.5')
            elif rec.status in ('leave', 'holiday'):
                present_days += Decimal('1.0')
                if rec.status == 'leave':
                    paid_leave_days += Decimal('1.0')
            elif rec.status == 'absent':
                absent_days += Decimal('1.0')
                lop_days += Decimal('1.0')
    else:
        # If no daily attendance entered, default to full working days present
        present_days = Decimal(str(total_working_days))

    # Mid-month joining proration
    if doj and doj > start_date and doj <= end_date:
        days_before_joining = 0
        for d in range(1, doj.day):
            if datetime.date(year, month, d).isoweekday() != 7:
                days_before_joining += 1
        lop_days += Decimal(str(days_before_joining))
        present_days = max(Decimal('0.0'), Decimal(str(total_working_days)) - lop_days)

    return {
        'present_days': present_days,
        'paid_leave_days': paid_leave_days,
        'absent_days': absent_days,
        'lop_days': lop_days,
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
    Supports statutory ₹15,000 wage ceiling if configured.
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
    employer_epf = (pf_wage * Decimal('0.0367')).quantize(Decimal('0.01'))
    employer_eps = (pf_wage * Decimal('0.0833')).quantize(Decimal('0.01'))

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


def compute_tds(gross_salary):
    """Computes monthly Tax Deducted at Source based on projected annual gross."""
    annual_gross = Decimal(str(gross_salary)) * Decimal('12')

    tax = Decimal('0.0')
    if annual_gross <= Decimal('250000'):
        tax = Decimal('0.0')
    elif annual_gross <= Decimal('500000'):
        tax = (annual_gross - Decimal('250000')) * Decimal('0.05')
        if tax <= Decimal('12500'):  # Section 87A rebate
            tax = Decimal('0.0')
    elif annual_gross <= Decimal('1000000'):
        tax = Decimal('12500') + (annual_gross - Decimal('500000')) * Decimal('0.20')
    else:
        tax = Decimal('112500') + (annual_gross - Decimal('1000000')) * Decimal('0.30')

    if tax > Decimal('0.0'):
        tax += tax * Decimal('0.04')  # 4% Health & Education Cess

    monthly_tax = tax / Decimal('12')
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
    Computes prorated gross salary from the employee's active salary assignment and attendance.
    """
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])

    assignment = EmployeeSalaryAssignment.objects.filter(
        employee=employee,
        effective_from__lte=end_date
    ).order_by('-effective_from').first()

    if not assignment:
        return Decimal('0.0'), None, Decimal('0.0')

    settings = get_hrms_settings(employee.tenant)
    working_days = Decimal(str(get_total_working_days(month, year, settings.lop_calculation_rule)))

    if working_days == Decimal('0.0'):
        return Decimal('0.0'), assignment, Decimal('0.0')

    breakdown = get_attendance_breakdown(employee, month, year, int(working_days))
    present_days = breakdown['present_days']

    computed_components = assignment.computed_components
    total_monthly_gross = Decimal('0.0')

    for comp_name, value in computed_components.items():
        total_monthly_gross += Decimal(str(value))

    proration_factor = present_days / working_days
    prorated_gross = (total_monthly_gross * proration_factor).quantize(Decimal('0.01'))

    return prorated_gross, assignment, proration_factor


def compute_advances_and_loans(employee, gross_available, allow_negative=False):
    """
    Calculates monthly installment recovery for active advances and loans.
    Deduction is strictly capped at the outstanding balance.
    """
    active_loans = EmployeeAdvanceLoan.objects.filter(
        employee=employee,
        status='active',
        outstanding_balance__gt=0
    ).order_by('disbursement_date')

    advance_deduction = Decimal('0.00')
    loan_deduction = Decimal('0.00')
    available = Decimal(str(gross_available))

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

    return advance_deduction, loan_deduction


def compute_payslip_for_employee(employee, payroll_run):
    """
    Computes a complete, transparent payslip for an employee within a payroll run.
    """
    month = payroll_run.month
    year = payroll_run.year
    tenant = payroll_run.tenant
    settings = get_hrms_settings(tenant)

    working_days = get_total_working_days(month, year, settings.lop_calculation_rule)
    breakdown = get_attendance_breakdown(employee, month, year, working_days)

    prorated_gross, assignment, proration_factor = compute_gross(employee, month, year)

    if not assignment:
        return None

    # Overtime
    ot_hours, ot_amount = compute_overtime(employee, month, year)
    gross_with_ot = (prorated_gross + ot_amount).quantize(Decimal('0.01'))

    # Basic component lookup for PF
    basic_comp = assignment.salary_structure.components.filter(is_basic=True).first()
    if basic_comp:
        base_basic_str = assignment.computed_components.get(basic_comp.name, '0.0')
        basic_salary = (Decimal(str(base_basic_str)) * proration_factor).quantize(Decimal('0.01'))
    else:
        basic_salary = Decimal('0.0')

    # Statutory Calculations
    pf_details = compute_pf(basic_salary, settings)
    esi_details = compute_esi(gross_with_ot, settings)
    tds_val = compute_tds(gross_with_ot)
    pt_val = compute_pt(gross_with_ot, employee.work_state)

    # Statutory Deductions subtotal
    statutory_deductions = (
        pf_details['employee_pf'] +
        esi_details['employee_esi'] +
        tds_val +
        pt_val
    )

    available_for_loans = max(Decimal('0.00'), gross_with_ot - statutory_deductions)
    advance_rec, loan_rec = compute_advances_and_loans(employee, available_for_loans, settings.allow_negative_salary)

    total_deductions = (statutory_deductions + advance_rec + loan_rec).quantize(Decimal('0.01'))
    net_salary = (gross_with_ot - total_deductions).quantize(Decimal('0.01'))

    # Employer Contributions
    employer_total = (pf_details['employer_pf'] + esi_details['employer_esi']).quantize(Decimal('0.01'))

    # Itemized Breakdown
    earnings = {}
    for cname, cval in assignment.computed_components.items():
        earnings[cname] = str((Decimal(str(cval)) * proration_factor).quantize(Decimal('0.01')))

    if ot_amount > 0:
        earnings['Overtime'] = str(ot_amount)

    deductions = {
        'PF': str(pf_details['employee_pf']),
        'ESI': str(esi_details['employee_esi']),
        'TDS': str(tds_val),
        'PT': str(pt_val),
    }
    if advance_rec > 0:
        deductions['Salary Advance Recovery'] = str(advance_rec)
    if loan_rec > 0:
        deductions['Loan Recovery'] = str(loan_rec)

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
        gross_salary=gross_with_ot,
        earnings=earnings,
        deductions=deductions,
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
    Executes the monthly payroll computation for all active employees.
    Atomic, deterministic, and traceable.
    """
    try:
        run = PayrollRun.objects.get(id=payroll_run_id)
    except PayrollRun.DoesNotExist:
        return

    with transaction.atomic():
        Payslip.objects.filter(payroll_run=run).delete()

        # Eligible employees: joined on or before month-end, not resigned or terminated
        days_in_month = calendar.monthrange(run.year, run.month)[1]
        end_date = datetime.date(run.year, run.month, days_in_month)

        employees = Employee.objects.filter(
            tenant=run.tenant,
            date_of_joining__lte=end_date,
        ).exclude(status__in=['resigned', 'terminated'])

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

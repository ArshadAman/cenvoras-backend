import datetime
import calendar
from decimal import Decimal
from django.db.models import Q
from hr.models import AttendanceRecord, EmployeeSalaryAssignment, ProfessionalTaxSlab

def get_present_days(employee, month, year):
    start_date = datetime.date(year, month, 1)
    end_date = datetime.date(year, month, calendar.monthrange(year, month)[1])
    
    records = AttendanceRecord.objects.filter(
        employee=employee,
        date__range=(start_date, end_date)
    )
    
    total_days = Decimal('0.0')
    for rec in records:
        if rec.status == 'present':
            total_days += Decimal('1.0')
        elif rec.status == 'half_day':
            total_days += Decimal('0.5')
        elif rec.status in ('leave', 'holiday'):
            total_days += Decimal('1.0')
    return total_days

def get_total_working_days(month, year):
    days_in_month = calendar.monthrange(year, month)[1]
    working_days = 0
    for day in range(1, days_in_month + 1):
        if datetime.date(year, month, day).isoweekday() != 7: # Mon-Sat
            working_days += 1
    return working_days

def compute_gross(employee, month, year):
    start_date = datetime.date(year, month, 1)
    
    assignment = EmployeeSalaryAssignment.objects.filter(
        employee=employee,
        effective_from__lte=start_date
    ).order_by('-effective_from').first()
    
    if not assignment:
        return Decimal('0.0'), None, Decimal('0.0')
        
    working_days = Decimal(get_total_working_days(month, year))
    if working_days == Decimal('0.0'):
        return Decimal('0.0'), assignment, Decimal('0.0')
        
    present_days = get_present_days(employee, month, year)
    
    computed_components = assignment.computed_components
    total_monthly_gross = Decimal('0.0')
    
    for comp_name, value in computed_components.items():
        total_monthly_gross += Decimal(str(value))
        
    proration_factor = present_days / working_days
    prorated_gross = (total_monthly_gross * proration_factor).quantize(Decimal('0.01'))
    
    return prorated_gross, assignment, proration_factor

def compute_pf(basic_salary):
    basic = Decimal(str(basic_salary))
    emp_pf = (basic * Decimal('0.12')).quantize(Decimal('0.01'))
    employer_pf = (basic * Decimal('0.12')).quantize(Decimal('0.01'))
    employer_epf = (basic * Decimal('0.0367')).quantize(Decimal('0.01'))
    employer_eps = (basic * Decimal('0.0833')).quantize(Decimal('0.01'))
    
    return {
        'employee_pf': emp_pf,
        'employer_pf': employer_pf,
        'employer_epf': employer_epf,
        'employer_eps': employer_eps
    }

def compute_esi(gross_salary):
    gross = Decimal(str(gross_salary))
    if gross <= Decimal('21000'):
        emp_esi = (gross * Decimal('0.0075')).quantize(Decimal('0.01'))
        employer_esi = (gross * Decimal('0.0325')).quantize(Decimal('0.01'))
    else:
        emp_esi = Decimal('0.00')
        employer_esi = Decimal('0.00')
        
    return {
        'employee_esi': emp_esi,
        'employer_esi': employer_esi
    }

def compute_tds(gross_salary):
    annual_gross = Decimal(str(gross_salary)) * Decimal('12')
    
    tax = Decimal('0.0')
    if annual_gross <= Decimal('250000'):
        tax = Decimal('0.0')
    elif annual_gross <= Decimal('500000'):
        tax = (annual_gross - Decimal('250000')) * Decimal('0.05')
        if tax <= Decimal('12500'):  # 87A rebate
            tax = Decimal('0.0')
    elif annual_gross <= Decimal('1000000'):
        tax = Decimal('12500') + (annual_gross - Decimal('500000')) * Decimal('0.20')
    else:
        tax = Decimal('112500') + (annual_gross - Decimal('1000000')) * Decimal('0.30')
        
    if tax > Decimal('0.0'):
        tax += tax * Decimal('0.04')  # Health and Education Cess
        
    monthly_tax = tax / Decimal('12')
    return monthly_tax.quantize(Decimal('0.01'))

def compute_pt(gross_salary, work_state):
    gross = Decimal(str(gross_salary))
    slab = ProfessionalTaxSlab.objects.filter(
        state_name__iexact=work_state,
        lower_bound__lte=gross
    ).filter(
        Q(upper_bound__isnull=True) | Q(upper_bound__gte=gross)
    ).first()
    
    if slab:
        return Decimal(str(slab.pt_amount))
    return Decimal('0.00')


from django.db import transaction
from hr.models import PayrollRun, Employee, Payslip

def compute_payslip_for_employee(employee, payroll_run):
    month = payroll_run.month
    year = payroll_run.year
    tenant = payroll_run.tenant
    
    prorated_gross, assignment, proration_factor = compute_gross(employee, month, year)
    
    if not assignment:
        return None
        
    basic_comp = assignment.salary_structure.components.filter(is_basic=True).first()
    if basic_comp:
        base_basic_str = assignment.computed_components.get(basic_comp.name, '0.0')
        basic_salary = (Decimal(str(base_basic_str)) * proration_factor).quantize(Decimal('0.01'))
    else:
        basic_salary = Decimal('0.0')
        
    pf_details = compute_pf(basic_salary)
    esi_details = compute_esi(prorated_gross)
    tds_val = compute_tds(prorated_gross)
    pt_val = compute_pt(prorated_gross, employee.work_state)
    
    net_salary = (
        prorated_gross 
        - pf_details['employee_pf'] 
        - esi_details['employee_esi'] 
        - tds_val 
        - pt_val
    ).quantize(Decimal('0.01'))
    
    earnings = {}
    for cname, cval in assignment.computed_components.items():
        earnings[cname] = str((Decimal(str(cval)) * proration_factor).quantize(Decimal('0.01')))
        
    deductions = {
        'PF': str(pf_details['employee_pf']),
        'ESI': str(esi_details['employee_esi']),
        'TDS': str(tds_val),
        'PT': str(pt_val)
    }
    
    payslip = Payslip(
        tenant=tenant,
        payroll_run=payroll_run,
        employee=employee,
        present_days=get_present_days(employee, month, year),
        total_working_days=get_total_working_days(month, year),
        gross_salary=prorated_gross,
        earnings=earnings,
        deductions=deductions,
        employee_pf=pf_details['employee_pf'],
        employee_esi=esi_details['employee_esi'],
        tds=tds_val,
        professional_tax=pt_val,
        employer_pf=pf_details['employer_pf'],
        employer_epf=pf_details['employer_epf'],
        employer_eps=pf_details['employer_eps'],
        employer_esi=esi_details['employer_esi'],
        net_salary=net_salary
    )
    return payslip

def run_payroll(payroll_run_id):
    try:
        run = PayrollRun.objects.get(id=payroll_run_id)
    except PayrollRun.DoesNotExist:
        return
        
    with transaction.atomic():
        Payslip.objects.filter(payroll_run=run).delete()
        
        employees = Employee.objects.filter(tenant=run.tenant, status='active')
        payslips_to_create = []
        
        total_gross = Decimal('0.0')
        total_net = Decimal('0.0')
        
        for emp in employees:
            payslip = compute_payslip_for_employee(emp, run)
            if payslip:
                payslips_to_create.append(payslip)
                total_gross += payslip.gross_salary
                total_net += payslip.net_salary
                
        Payslip.objects.bulk_create(payslips_to_create)
        
        run.total_gross = total_gross
        run.total_net = total_net
        run.save(update_fields=['total_gross', 'total_net'])

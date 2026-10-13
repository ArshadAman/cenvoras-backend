# Leave service — leave day calculation helpers
# Implemented in task 9.1

import datetime
from decimal import Decimal
from ..models import AttendanceRecord, LeaveBalance

def compute_leave_days(start_date, end_date, employee):
    """
    Counts calendar days inclusive, excluding Sundays and days already 
    marked as 'Holiday' in AttendanceRecord for that employee.
    Requirement 5.2
    """
    if start_date > end_date:
        return Decimal('0.0')
    
    current_date = start_date
    leave_days = Decimal('0.0')
    
    # Pre-fetch holidays to avoid querying inside the loop
    holidays = AttendanceRecord.objects.filter(
        employee=employee,
        date__range=(start_date, end_date),
        status='holiday'
    ).values_list('date', flat=True)
    
    holiday_set = set(holidays)

    while current_date <= end_date:
        # isoweekday() == 7 means Sunday
        if current_date.isoweekday() != 7 and current_date not in holiday_set:
            leave_days += Decimal('1.0')
        current_date += datetime.timedelta(days=1)
        
    return leave_days


def get_or_init_leave_balance(employee, leave_type, year):
    """
    Returns existing LeaveBalance or creates one initialised to leave_type.annual_entitlement.
    Requirement 6.2
    """
    balance, created = LeaveBalance.objects.get_or_create(
        employee=employee,
        leave_type=leave_type,
        year=year,
        defaults={'balance': leave_type.annual_entitlement}
    )
    return balance


def ensure_employee_leave_balances(employee, year=None):
    """
    Ensure LeaveBalance rows exist for every paid leave type for the employee/year.
    Called on employee create and leave-type create so the UI never shows an empty list.
    """
    from ..models import LeaveType
    if year is None:
        year = datetime.date.today().year
    leave_types = LeaveType.objects.filter(tenant=employee.tenant, is_paid=True)
    balances = []
    for lt in leave_types:
        balances.append(get_or_init_leave_balance(employee, lt, year))
    return balances


def ensure_leave_type_balances(leave_type, year=None):
    """Init balances for all active employees when a new paid leave type is created."""
    from ..models import Employee
    if not leave_type.is_paid:
        return []
    if year is None:
        year = datetime.date.today().year
    employees = Employee.objects.filter(tenant=leave_type.tenant, status='active')
    balances = []
    for emp in employees:
        balances.append(get_or_init_leave_balance(emp, leave_type, year))
    return balances

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

"""
Payroll Exceptions Engine — Identifies pre-approval anomalies and integrity issues.
"""
import datetime
import calendar
from decimal import Decimal
from django.utils import timezone
from hr.models import (
    Employee, PayrollRun, PayrollException,
    AttendanceRecord, LeaveApplication, OvertimeRecord,
    EmployeeSalaryAssignment
)

def scan_payroll_exceptions(payroll_run_id):
    """
    Scans all employees eligible for a payroll run and logs PayrollException records.
    Returns a dict with critical_count, warning_count, and total_count.
    """
    run = PayrollRun.objects.get(id=payroll_run_id)
    tenant = run.tenant
    month = run.month
    year = run.year

    start_date = datetime.date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    end_date = datetime.date(year, month, days_in_month)

    # Clear previous exceptions for this run
    PayrollException.objects.filter(payroll_run=run).delete()

    active_employees = Employee.objects.filter(
        tenant=tenant,
        date_of_joining__lte=end_date,
    ).exclude(status__in=['resigned', 'terminated'])

    exceptions_to_create = []

    for emp in active_employees:
        # 1. Missing Bank Details (Critical for payouts)
        if not emp.bank_account_number or not emp.bank_ifsc:
            missing_fields = []
            if not emp.bank_account_number:
                missing_fields.append("Account Number")
            if not emp.bank_ifsc:
                missing_fields.append("IFSC")
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='critical',
                    code='MISSING_BANK_INFO',
                    message=f"Missing bank information ({', '.join(missing_fields)}). Direct deposit will fail.",
                )
            )

        # 2. Missing PAN (Warning: subject to 20% higher TDS deduction under Sec 206AA)
        if not emp.pan_number:
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='warning',
                    code='MISSING_PAN',
                    message="PAN number is missing. May attract mandatory 20% TDS under Section 206AA.",
                )
            )

        # 3. Missing Salary Structure Assignment
        assignment = EmployeeSalaryAssignment.objects.filter(
            employee=emp,
            effective_from__lte=end_date,
        ).order_by('-effective_from').first()

        if not assignment:
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='critical',
                    code='NO_SALARY_STRUCTURE',
                    message="No active salary structure assigned to this employee for this period.",
                )
            )

        # 4. Mid-Month Joiner
        if start_date < emp.date_of_joining <= end_date:
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='warning',
                    code='MID_MONTH_JOINER',
                    message=f"Employee joined on {emp.date_of_joining}. Salary will be prorated from joining date.",
                )
            )

        # 5. Attendance Not Submitted / Missing Days
        attendance_count = AttendanceRecord.objects.filter(
            employee=emp,
            date__range=(max(start_date, emp.date_of_joining), end_date),
        ).count()

        if attendance_count == 0:
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='warning',
                    code='ATTENDANCE_MISSING',
                    message="No daily attendance records found for this period. Default full working attendance applied.",
                )
            )

        # 6. Unapproved Leave Requests
        unapproved_leaves = LeaveApplication.objects.filter(
            employee=emp,
            status='pending',
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        if unapproved_leaves.exists():
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='warning',
                    code='UNAPPROVED_LEAVE',
                    message=f"{unapproved_leaves.count()} leave request(s) remain in 'Pending' status. Attendance deduction may be inaccurate.",
                )
            )

        # 7. Unapproved Overtime
        unapproved_overtime = OvertimeRecord.objects.filter(
            employee=emp,
            status='pending',
            date__range=(start_date, end_date),
        )
        if unapproved_overtime.exists():
            exceptions_to_create.append(
                PayrollException(
                    tenant=tenant,
                    payroll_run=run,
                    employee=emp,
                    severity='warning',
                    code='UNAPPROVED_OVERTIME',
                    message=f"{unapproved_overtime.count()} overtime record(s) pending approval will not be included in earnings.",
                )
            )

    # 8. Check for negative net salaries in payslips (if already computed)
    from hr.models import Payslip
    negative_payslips = Payslip.objects.filter(payroll_run=run, net_salary__lt=0)
    for ps in negative_payslips:
        exceptions_to_create.append(
            PayrollException(
                tenant=tenant,
                payroll_run=run,
                employee=ps.employee,
                severity='critical',
                code='NEGATIVE_NET_SALARY',
                message=f"Computed net salary is negative (₹{ps.net_salary}). Deductions exceed total gross earnings.",
            )
        )

    if exceptions_to_create:
        PayrollException.objects.bulk_create(exceptions_to_create)

    critical_count = sum(1 for e in exceptions_to_create if e.severity == 'critical')
    warning_count = sum(1 for e in exceptions_to_create if e.severity == 'warning')

    return {
        'total': len(exceptions_to_create),
        'critical': critical_count,
        'warning': warning_count,
    }

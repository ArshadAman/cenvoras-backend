# HR data models — implemented in task 2

import uuid

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Task 2.1 models: Department, Designation, Employee, AttendanceRecord
# (to be implemented in task 2.1 — placeholders kept for FK references below)
# ---------------------------------------------------------------------------

class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_departments',
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')

    def __str__(self):
        return self.name


class Designation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_designations',
    )
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')

    def __str__(self):
        return self.name


class Employee(models.Model):
    EMPLOYMENT_TYPE = [
        ('full_time', 'Full-Time'),
        ('part_time', 'Part-Time'),
        ('contract', 'Contract'),
    ]
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('on_leave', 'On Leave'),
        ('resigned', 'Resigned'),
        ('terminated', 'Terminated'),
        ('inactive', 'Inactive'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_employees',
    )
    employee_code = models.CharField(max_length=20)  # EMP-0001, auto-generated
    full_name = models.CharField(max_length=255)
    father_mother_name = models.CharField(max_length=255, blank=True, null=True)
    date_of_birth = models.DateField()
    date_of_joining = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name='employees',
    )
    designation = models.ForeignKey(
        Designation,
        on_delete=models.PROTECT,
        related_name='employees',
    )
    branch = models.ForeignKey(
        'inventory.Warehouse',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
    )
    reporting_manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subordinates',
    )
    work_location = models.CharField(max_length=255, blank=True, null=True)
    work_state = models.CharField(max_length=100)  # For PT slab lookup
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='active')

    # Optional fields
    profile_photo = models.ImageField(upload_to='employee_photos/', blank=True, null=True)
    profile_photo_url = models.URLField(max_length=500, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    personal_email = models.EmailField(blank=True, null=True)
    personal_phone = models.CharField(max_length=15, blank=True, null=True)
    pan_number = models.CharField(max_length=10, blank=True, null=True)
    aadhaar_number = models.CharField(max_length=12, blank=True, null=True)
    bank_account_number = models.CharField(max_length=20, blank=True, null=True)
    bank_ifsc = models.CharField(max_length=11, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    account_holder_name = models.CharField(max_length=255, blank=True, null=True)
    uan = models.CharField(max_length=12, blank=True, null=True)
    esi_ip_number = models.CharField(max_length=20, blank=True, null=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employee_profile',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'employee_code')

    def save(self, *args, **kwargs):
        if not self.employee_code:
            last = (
                Employee.objects.filter(tenant=self.tenant)
                .order_by('-employee_code')
                .first()
            )
            if last and last.employee_code.startswith('EMP-'):
                next_num = int(last.employee_code[4:]) + 1
            else:
                next_num = 1
            self.employee_code = f'EMP-{next_num:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.employee_code} — {self.full_name}'


class EmployeeSalaryHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_salary_histories',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='salary_history',
    )
    effective_date = models.DateField()
    previous_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    new_salary = models.DecimalField(max_digits=12, decimal_places=2)
    salary_structure = models.ForeignKey(
        'SalaryStructure',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='salary_histories',
    )
    reason = models.CharField(max_length=255, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_salary_revisions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date', '-created_at']

    def __str__(self):
        return f"{self.employee.employee_code} — {self.effective_date}: ₹{self.previous_salary} -> ₹{self.new_salary}"


class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('half_day', 'Half-Day'),
        ('leave', 'Leave'),
        ('holiday', 'Holiday'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_attendance',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')

    def __str__(self):
        return f'{self.employee} — {self.date} — {self.status}'


# ---------------------------------------------------------------------------
# Task 2.2 models: LeaveType, LeaveBalance, LeaveApplication
# Requirements: 5.1, 5.2, 6.1, 6.2
# ---------------------------------------------------------------------------

class LeaveType(models.Model):
    """
    Configurable leave category (e.g. Casual Leave, Sick Leave) with an
    annual entitlement quota.  Scoped per tenant.
    Requirement 5.1
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_leave_types',
    )
    name = models.CharField(max_length=100)
    annual_entitlement = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        help_text='Number of days an employee is entitled to per calendar year.',
    )
    is_paid = models.BooleanField(
        default=True,
        help_text='Whether days taken under this leave type are paid.',
    )
    carry_forward_max_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=0,
        help_text='Maximum unused days allowed to carry forward to the next year.',
    )
    max_consecutive_days = models.PositiveSmallIntegerField(
        default=0,
        help_text='Maximum consecutive days allowed in a single application (0 = unlimited).',
    )
    requires_approval = models.BooleanField(
        default=True,
        help_text='Whether leave applications under this type require managerial approval.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'name')

    def __str__(self):
        return f'{self.name} ({"Paid" if self.is_paid else "Unpaid"})'


class LeaveBalance(models.Model):
    """
    Tracks the remaining leave balance for a specific employee, leave type,
    and calendar year.
    Requirement 5.2
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='leave_balances',
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.CASCADE,
        related_name='balances',
    )
    year = models.IntegerField(
        help_text='Calendar year this balance applies to (e.g. 2025).',
    )
    balance = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        help_text='Remaining leave days available for this employee.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')

    def __str__(self):
        return (
            f'{self.employee} — {self.leave_type.name} — '
            f'{self.year}: {self.balance} days'
        )


class LeaveApplication(models.Model):
    """
    A formal request for an employee to be absent under a specific leave type.
    computed_days is set by the leave service on creation.
    lwp_days records any excess days beyond the available balance (Leave Without Pay).
    Requirements: 6.1, 6.2
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_leave_applications',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='leave_applications',
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name='applications',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    # Computed by leave_service.compute_leave_days() on creation (excludes Sundays
    # and days already marked Holiday in AttendanceRecord).
    computed_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=0,
        help_text='Number of leave days computed from start/end date range.',
    )
    # Days beyond the available LeaveBalance that are treated as Leave Without Pay.
    lwp_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=0,
        help_text='Leave Without Pay days (excess beyond available balance).',
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f'{self.employee} — {self.leave_type.name} — '
            f'{self.start_date} to {self.end_date} ({self.status})'
        )


# ---------------------------------------------------------------------------
# Task 2.3 models: SalaryStructure, SalaryComponent, EmployeeSalaryAssignment
# Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6
# ---------------------------------------------------------------------------

class SalaryStructure(models.Model):
    """
    Named template defining the fixed and variable components of an employee's
    compensation (Basic, HRA, Conveyance, Special Allowance, etc.).
    Requirement 7.1
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_salary_structures',
    )
    name = models.CharField(max_length=100)
    description = models.TextField(
        blank=True,
        help_text='Optional description of this salary structure.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'name')

    def __str__(self):
        return self.name


class SalaryComponent(models.Model):
    """
    A configurable salary component (earning or deduction) within a SalaryStructure.
    """

    TYPE_CHOICES = [
        ('earning', 'Earning'),
        ('deduction', 'Deduction'),
    ]

    COMPONENT_TYPE = [
        ('fixed', 'Fixed Amount'),
        ('pct_basic', 'Percentage of Basic'),
        ('pct_gross', 'Percentage of Gross'),
        ('pct_ctc', 'Percentage of CTC'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.CASCADE,
        related_name='components',
    )
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=15, choices=TYPE_CHOICES, default='earning')
    component_type = models.CharField(max_length=15, choices=COMPONENT_TYPE, default='fixed')
    calculation_base = models.CharField(max_length=50, blank=True, default='basic')
    is_basic = models.BooleanField(
        default=False,
        help_text='Designates this component as the Basic salary component.',
    )
    # For fixed: INR amount. For pct_*: percentage value (e.g. 40.00 = 40%).
    value = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    percentage = models.DecimalField(max_digits=7, decimal_places=4, default=0)

    # Statutory & Tax Flags
    is_taxable = models.BooleanField(default=True)
    pf_applicable = models.BooleanField(default=False)
    esi_applicable = models.BooleanField(default=False)
    pt_applicable = models.BooleanField(default=False)
    tds_applicable = models.BooleanField(default=False)

    # Contributions
    employee_contribution_pct = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    employer_contribution_pct = models.DecimalField(max_digits=6, decimal_places=3, default=0)

    is_active = models.BooleanField(default=True)
    order = models.IntegerField(
        default=0,
        help_text='Display ordering of this component within the salary structure.',
    )

    class Meta:
        ordering = ['salary_structure', 'order', 'name']

    def __str__(self):
        return f'{self.salary_structure.name} — {self.name} ({self.type})'


class EmployeeSalaryAssignment(models.Model):
    """
    Links an employee to a SalaryStructure with an effective date and CTC.
    computed_components stores a JSON snapshot of each component's INR value
    at the time of assignment, enabling historical payroll recomputation.
    Requirements: 7.4, 7.5, 7.6
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_salary_assignments',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='salary_assignments',
    )
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.PROTECT,
        related_name='assignments',
    )
    effective_from = models.DateField()
    monthly_ctc = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Monthly Cost to Company (INR) used as the basis for percentage components.',
    )
    # JSON snapshot: {component_name: computed_inr_amount, ...}
    # Stored at assignment time so historical payroll runs remain accurate.
    computed_components = models.JSONField(
        default=dict,
        help_text='Snapshot of computed INR values per component at assignment time.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from']

    def __str__(self):
        return (
            f'{self.employee} — {self.salary_structure.name} '
            f'(from {self.effective_from})'
        )


# ---------------------------------------------------------------------------
# Overtime, Advances & Loans models
# ---------------------------------------------------------------------------

class OvertimeRecord(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_overtime_records',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='overtime_records',
    )
    date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.5)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_overtimes',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.employee.employee_code} — {self.date}: {self.hours} hrs ({self.status})"


class EmployeeAdvanceLoan(models.Model):
    TYPE_CHOICES = [
        ('advance', 'Salary Advance'),
        ('loan', 'Employee Loan'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('fully_recovered', 'Fully Recovered'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_advances_loans',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='advances_loans',
    )
    record_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='advance')
    original_amount = models.DecimalField(max_digits=12, decimal_places=2)
    outstanding_balance = models.DecimalField(max_digits=12, decimal_places=2)
    monthly_installment = models.DecimalField(max_digits=12, decimal_places=2)
    disbursement_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    reason = models.TextField(blank=True)
    disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='disbursed_advances_loans',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-disbursement_date', '-created_at']

    def __str__(self):
        return f"{self.employee.full_name} — {self.get_record_type_display()} ₹{self.original_amount} (Bal: ₹{self.outstanding_balance})"


# ---------------------------------------------------------------------------
# Task 2.4 models: PayrollRun, Payslip, ProfessionalTaxSlab, PayrollException
# Requirements: 8.1, 8.8, 9.1
# ---------------------------------------------------------------------------

class PayrollRun(models.Model):
    """
    Represents a monthly payroll computation run for a tenant.
    Only one run per (tenant, month, year) is allowed.
    Lifecycle: draft -> calculating -> calculated -> pending_approval -> approved -> paid -> locked
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('calculating', 'Calculating'),
        ('calculated', 'Calculated'),
        ('completed', 'Completed'),
        ('processing', 'Processing'),
        ('pending_approval', 'Pending Approval'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('locked', 'Locked'),
        ('finalised', 'Finalised'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_payroll_runs',
    )
    # month: 1–12
    month = models.PositiveSmallIntegerField(
        help_text='Month of the payroll run (1 = January, 12 = December).',
    )
    year = models.PositiveIntegerField(
        help_text='Calendar year of the payroll run (e.g. 2025).',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
    )
    total_gross = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of gross salaries for all employees in this run.',
    )
    total_deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of employee deductions for all employees in this run.',
    )
    total_net = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of net salaries for all employees in this run.',
    )
    total_employer_contributions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of employer contributions (PF, ESI) for this run.',
    )
    # Workflow timestamps & actors
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_payroll_runs',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paid_payroll_runs',
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_account = models.ForeignKey(
        'ledger.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='payroll_payments',
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='locked_payroll_runs',
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    finalised_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when this payroll run was finalised.',
    )
    is_reopened = models.BooleanField(default=False)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reopened_payroll_runs',
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        return f'PayrollRun {self.month}/{self.year} — {self.tenant} ({self.status})'


class Payslip(models.Model):
    """
    The computed payslip for a single employee within a PayrollRun.
    Stores all earnings, deductions, employer contributions, and net salary.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_payslips',
    )
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='payslips',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name='payslips',
    )

    # Attendance proration inputs
    present_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        help_text='Effective present days (present=1.0, half_day=0.5, paid_leave=1.0).',
    )
    total_working_days = models.PositiveSmallIntegerField(
        help_text='Total working days in the payroll month.',
    )
    absent_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    paid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    lop_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Earnings
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    earnings = models.JSONField(
        default=dict,
        help_text='Breakdown of each earnings component (prorated INR amounts).',
    )

    # Deductions JSON snapshot
    deductions = models.JSONField(
        default=dict,
        help_text='Breakdown of each deduction.',
    )

    # Employee statutory deductions
    employee_pf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employee_esi = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tds = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    professional_tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    advance_recovery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    loan_recovery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Employer contributions (not deducted from employee net, recorded for compliance)
    employer_pf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_epf = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_eps = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_esi = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_total_contribution = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Net salary
    net_salary = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Net take-home salary after all deductions.',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('payroll_run', 'employee')
        ordering = ['employee__employee_code']

    def __str__(self):
        return (
            f'Payslip — {self.employee} — '
            f'{self.payroll_run.month}/{self.payroll_run.year}'
        )


class LoanRecoveryLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_recovery_logs',
    )
    loan = models.ForeignKey(
        EmployeeAdvanceLoan,
        on_delete=models.CASCADE,
        related_name='recoveries',
    )
    payslip = models.ForeignKey(
        Payslip,
        on_delete=models.CASCADE,
        related_name='loan_recoveries',
        null=True,
        blank=True,
    )
    recovery_date = models.DateField()
    amount_recovered = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recovery_date']

    def __str__(self):
        return f"Recovery ₹{self.amount_recovered} for {self.loan} on {self.recovery_date}"


class PayrollException(models.Model):
    SEVERITY_CHOICES = [
        ('critical', 'Critical'),
        ('warning', 'Warning'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_payroll_exceptions',
    )
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='exceptions',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='payroll_exceptions',
        null=True,
        blank=True,
    )
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='warning')
    code = models.CharField(max_length=50)  # MISSING_BANK, NEGATIVE_NET, MISSING_PAN, etc.
    message = models.TextField()
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['severity', 'created_at']

    def __str__(self):
        return f"[{self.severity.upper()}] {self.code}: {self.message}"


class HRDocument(models.Model):
    DOC_TYPES = [
        ('offer_letter', 'Offer Letter'),
        ('appointment_letter', 'Appointment Letter'),
        ('id_proof', 'ID Proof'),
        ('tax_form', 'Tax Declaration Form'),
        ('resignation', 'Resignation Letter'),
        ('appraisal', 'Appraisal Letter'),
        ('other', 'Other Document'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_documents',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    title = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30, choices=DOC_TYPES, default='other')
    file = models.FileField(upload_to='hr_documents/', null=True, blank=True)
    file_url = models.URLField(max_length=500, blank=True, null=True)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.employee_code} - {self.title}"


class HRMSSettings(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hrms_settings',
    )
    payroll_frequency = models.CharField(max_length=20, default='monthly')
    payroll_cutoff_day = models.PositiveSmallIntegerField(default=25)
    default_working_days = models.PositiveSmallIntegerField(default=26)
    lop_calculation_rule = models.CharField(
        max_length=30,
        choices=[
            ('working_days', 'Working Days (Excluding Weekends)'),
            ('calendar_days', 'Calendar Days in Month'),
            ('fixed_30', 'Fixed 30 Days Basis'),
        ],
        default='working_days',
    )
    overtime_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.5)
    allow_negative_salary = models.BooleanField(default=False)
    salary_rounding = models.CharField(
        max_length=20,
        choices=[('nearest_one', 'Nearest ₹1'), ('nearest_ten', 'Nearest ₹10'), ('exact', 'Exact 2 Decimals')],
        default='nearest_one',
    )

    # Statutory Rates (Configurable placeholders)
    pf_employee_rate = models.DecimalField(max_digits=5, decimal_places=2, default=12.0)
    pf_employer_rate = models.DecimalField(max_digits=5, decimal_places=2, default=12.0)
    pf_wage_ceiling = models.DecimalField(max_digits=10, decimal_places=2, default=15000.0)
    pf_apply_ceiling = models.BooleanField(default=False)

    esi_employee_rate = models.DecimalField(max_digits=5, decimal_places=3, default=0.75)
    esi_employer_rate = models.DecimalField(max_digits=5, decimal_places=3, default=3.25)
    esi_wage_ceiling = models.DecimalField(max_digits=10, decimal_places=2, default=21000.0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"HRMS Settings for {self.tenant}"


class ProfessionalTaxSlab(models.Model):
    """
    State-wise Professional Tax slab.  Global table — no tenant FK.
    upper_bound is nullable for the top slab (no upper limit).
    Seeded via the `seed_pt_slabs` management command.
    Requirements: 9.1, 9.2, 9.3, 9.4
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state_name = models.CharField(
        max_length=100,
        help_text='Indian state name (e.g. "Maharashtra").',
    )
    lower_bound = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text='Minimum monthly gross salary (INR) for this slab to apply.',
    )
    # Null indicates the top slab with no upper limit.
    upper_bound = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Maximum monthly gross salary (INR) for this slab; null = no upper limit.',
    )
    pt_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text='Professional Tax amount (INR) to deduct for this slab.',
    )

    class Meta:
        ordering = ['state_name', 'lower_bound']

    def __str__(self):
        upper = f'₹{self.upper_bound}' if self.upper_bound is not None else '∞'
        return (
            f'{self.state_name}: ₹{self.lower_bound}–{upper} → ₹{self.pt_amount}'
        )


class EmployeeTask(models.Model):
    """
    Assigned tasks to employees from HR.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='assigned_tasks')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} - {self.employee.full_name}"


class EmployeeQuery(models.Model):
    """
    Queries raised by employees to HR.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='queries')
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='resolved_queries')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.subject} - {self.employee.full_name}"


class EmployeeNotification(models.Model):
    """
    Notifications/announcements sent by HR/Admins to one or all employees.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hr_notifications',
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True,
        blank=True,  # null means broadcasted to ALL employees
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_notifications'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        target = self.employee.full_name if self.employee else "ALL"
        return f"{self.title} to {target}"


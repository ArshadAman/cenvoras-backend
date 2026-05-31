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
    work_state = models.CharField(max_length=100)  # For PT slab lookup
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')

    # Optional fields
    personal_email = models.EmailField(blank=True, null=True)
    personal_phone = models.CharField(max_length=15, blank=True, null=True)
    pan_number = models.CharField(max_length=10, blank=True, null=True)
    aadhaar_number = models.CharField(max_length=12, blank=True, null=True)
    bank_account_number = models.CharField(max_length=20, blank=True, null=True)
    bank_ifsc = models.CharField(max_length=11, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
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
    A single earnings component within a SalaryStructure.
    component_type determines how the INR value is derived:
      - fixed: stored directly as an INR amount
      - pct_basic: percentage of the Basic component
      - pct_gross: percentage of the total gross salary
    Exactly one component per structure must have is_basic=True.
    Requirements: 7.2, 7.3
    """

    COMPONENT_TYPE = [
        ('fixed', 'Fixed Amount'),
        ('pct_basic', 'Percentage of Basic'),
        ('pct_gross', 'Percentage of Gross'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_structure = models.ForeignKey(
        SalaryStructure,
        on_delete=models.CASCADE,
        related_name='components',
    )
    name = models.CharField(max_length=100)
    component_type = models.CharField(max_length=15, choices=COMPONENT_TYPE)
    # Exactly one component per SalaryStructure must have is_basic=True.
    is_basic = models.BooleanField(
        default=False,
        help_text='Designates this component as the Basic salary component.',
    )
    # For fixed: INR amount. For pct_*: percentage value (e.g. 40.00 = 40%).
    value = models.DecimalField(max_digits=10, decimal_places=4)
    # Controls the display order of components within a salary structure.
    order = models.IntegerField(
        default=0,
        help_text='Display ordering of this component within the salary structure.',
    )

    class Meta:
        ordering = ['salary_structure', 'order', 'name']

    def __str__(self):
        return f'{self.salary_structure.name} — {self.name}'


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
# Task 2.4 models: PayrollRun, Payslip, ProfessionalTaxSlab
# Requirements: 8.1, 8.8, 9.1
# ---------------------------------------------------------------------------

class PayrollRun(models.Model):
    """
    Represents a monthly payroll computation run for a tenant.
    Only one run per (tenant, month, year) is allowed.
    Once finalised, the run cannot be re-run (Requirement 8.9).
    Requirements: 8.1, 8.8, 8.9, 8.10
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('finalised', 'Finalised'),
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
        max_length=15,
        choices=STATUS_CHOICES,
        default='draft',
    )
    total_gross = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of gross salaries for all employees in this run.',
    )
    total_net = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Sum of net salaries for all employees in this run.',
    )
    # Set when the run is finalised; null until then.
    finalised_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when this payroll run was finalised.',
    )
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
    Requirements: 8.8, 10.1
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
        help_text='Total Mon–Sat working days in the payroll month.',
    )

    # Earnings
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    # JSON snapshot: {component_name: prorated_inr_amount}
    earnings = models.JSONField(
        default=dict,
        help_text='Breakdown of each earnings component (prorated INR amounts).',
    )

    # Deductions JSON snapshot: {deduction_name: amount}
    deductions = models.JSONField(
        default=dict,
        help_text='Breakdown of each deduction (PF, ESI, TDS, PT).',
    )

    # Employee statutory deductions
    employee_pf = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Employee PF contribution (12% of Basic).',
    )
    employee_esi = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Employee ESI contribution (0.75% of gross, if gross ≤ ₹21,000).',
    )
    tds = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Monthly TDS deducted (annualised slab / 12).',
    )
    professional_tax = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Professional Tax deducted per state slab.',
    )

    # Employer contributions (not deducted from employee net, recorded for compliance)
    employer_pf = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Total employer PF contribution (12% of Basic).',
    )
    employer_epf = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Employer EPF portion (3.67% of Basic).',
    )
    employer_eps = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Employer EPS portion (8.33% of Basic).',
    )
    employer_esi = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Employer ESI contribution (3.25% of gross, if gross ≤ ₹21,000).',
    )

    # Net salary: gross − employee_pf − employee_esi − tds − professional_tax
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


# Design Document: Employee Management ERP (`hr` app)

## Overview

The `hr` Django app adds a full-stack HR and payroll module to the Cenvoras platform. It follows the same multi-tenant, RBAC-gated, subscription-tiered architecture used by all existing apps. All data is scoped to `request.user.active_tenant`. The app is gated behind MID (basic HR) and PRO (payroll) subscription tiers via the existing `SubscriptionAccessMiddleware`. Every mutating operation is recorded via the existing `audit_log` app.

---

## Architecture

```
hr/
├── __init__.py
├── apps.py
├── admin.py
├── models.py                  # All HR data models
├── serializers.py             # DRF serializers
├── views.py                   # DRF ViewSets and function-based views
├── urls.py                    # URL routing
├── permissions.py             # Custom DRF permission classes
├── services/
│   ├── __init__.py
│   ├── payroll_engine.py      # Core payroll computation logic
│   ├── leave_service.py       # Leave day calculation helpers
│   ├── audit_service.py       # AuditLog write helpers
│   └── pdf_service.py         # Payslip PDF generation
├── tasks.py                   # Celery async tasks (payroll runs)
├── migrations/
│   └── 0001_initial.py
└── management/
    └── commands/
        └── seed_pt_slabs.py   # Seeds default Professional Tax slabs
```

### Integration Points

| Concern | Integration |
|---|---|
| Tenant scoping | `request.user.active_tenant` (same as all apps) |
| Subscription gate | `SubscriptionAccessMiddleware.FEATURE_RULES` — add `/api/hr/` entries |
| RBAC | Custom `HRPermission` DRF permission class + role checks in ViewSets |
| Audit trail | `audit_log.models.AuditLog` — written via `audit_service.py` |
| Async payroll | Celery task dispatched from `PayrollRunViewSet.run_payroll` action |
| Caching | Django cache (Redis) — dashboard summary cached 5 min per tenant |
| PDF generation | `reportlab` (same library used by billing PDF reports) |


---

## Data Models

All models use `UUIDField` as primary key and include a `tenant` FK to `settings.AUTH_USER_MODEL` for isolation. `created_by` stores the acting user (may differ from tenant for team members).

### Department

```python
class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_departments')
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')
```

### Designation

```python
class Designation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_designations')
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')
```

### Employee

```python
class Employee(models.Model):
    EMPLOYMENT_TYPE = [('full_time', 'Full-Time'), ('part_time', 'Part-Time'),
                       ('contract', 'Contract')]
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female'), ('O', 'Other')]
    STATUS_CHOICES = [('active', 'Active'), ('inactive', 'Inactive')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_employees')
    employee_code = models.CharField(max_length=20)          # EMP-0001, auto-generated
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField()
    date_of_joining = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE)
    department = models.ForeignKey(Department, on_delete=models.PROTECT,
                                   related_name='employees')
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT,
                                    related_name='employees')
    work_state = models.CharField(max_length=100)            # For PT slab lookup
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
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='employee_profile')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'employee_code')
```


### AttendanceRecord

```python
class AttendanceRecord(models.Model):
    STATUS_CHOICES = [('present', 'Present'), ('absent', 'Absent'),
                      ('half_day', 'Half-Day'), ('leave', 'Leave'),
                      ('holiday', 'Holiday')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_attendance')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE,
                                 related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('employee', 'date')
```

### LeaveType

```python
class LeaveType(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_leave_types')
    name = models.CharField(max_length=100)
    annual_entitlement = models.PositiveIntegerField()
    is_paid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')
```

### LeaveBalance

```python
class LeaveBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_leave_balances')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE,
                                 related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE,
                                   related_name='balances')
    year = models.PositiveIntegerField()
    balance = models.DecimalField(max_digits=5, decimal_places=1)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')
```

### LeaveApplication

```python
class LeaveApplication(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'),
                      ('rejected', 'Rejected')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_leave_applications')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE,
                                 related_name='leave_applications')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT,
                                   related_name='applications')
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    computed_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    lwp_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```


### SalaryStructure and SalaryComponent

```python
class SalaryStructure(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_salary_structures')
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'name')


class SalaryComponent(models.Model):
    COMPONENT_TYPE = [('fixed', 'Fixed Amount'), ('pct_basic', 'Percentage of Basic'),
                      ('pct_gross', 'Percentage of Gross')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.CASCADE,
                                         related_name='components')
    name = models.CharField(max_length=100)
    component_type = models.CharField(max_length=15, choices=COMPONENT_TYPE)
    is_basic = models.BooleanField(default=False)       # Exactly one per structure
    value = models.DecimalField(max_digits=10, decimal_places=4)
    # For fixed: INR amount. For pct_*: percentage (e.g. 40.00 = 40%)
```

### EmployeeSalaryAssignment

```python
class EmployeeSalaryAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_salary_assignments')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE,
                                 related_name='salary_assignments')
    salary_structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT,
                                         related_name='assignments')
    effective_from = models.DateField()
    monthly_ctc = models.DecimalField(max_digits=12, decimal_places=2)
    # Computed component values stored as JSON snapshot for historical accuracy
    computed_components = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from']
```

### PayrollRun and Payslip

```python
class PayrollRun(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('finalised', 'Finalised')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_payroll_runs')
    month = models.PositiveSmallIntegerField()   # 1–12
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    total_gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_net = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    finalised_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('tenant', 'month', 'year')


class Payslip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name='hr_payslips')
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE,
                                    related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT,
                                 related_name='payslips')
    present_days = models.DecimalField(max_digits=5, decimal_places=1)
    total_working_days = models.PositiveSmallIntegerField()
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    earnings = models.JSONField(default=dict)       # {component_name: amount}
    deductions = models.JSONField(default=dict)     # {deduction_name: amount}
    employee_pf = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employer_pf = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employer_epf = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employer_eps = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employee_esi = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employer_esi = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tds = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    professional_tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_salary = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('payroll_run', 'employee')
```


### ProfessionalTaxSlab

```python
class ProfessionalTaxSlab(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    state_name = models.CharField(max_length=100)
    lower_bound = models.DecimalField(max_digits=10, decimal_places=2)
    upper_bound = models.DecimalField(max_digits=10, decimal_places=2,
                                      null=True, blank=True)  # null = top slab
    pt_amount = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ['state_name', 'lower_bound']
```

PT slabs are global (not tenant-scoped) and seeded via `seed_pt_slabs` management command for: Maharashtra, Karnataka, West Bengal, Tamil Nadu, Andhra Pradesh, Telangana, Gujarat, and Madhya Pradesh.

---

## Employee Code Generation

Auto-generation of `EMP-{NNNN}` is handled in the `Employee.save()` override using a `SELECT MAX` query scoped to the tenant, padded to 4 digits. This runs inside the existing `ATOMIC_REQUESTS` transaction to prevent race conditions.

```python
def save(self, *args, **kwargs):
    if not self.employee_code:
        last = Employee.objects.filter(tenant=self.tenant)\
                               .order_by('-employee_code').first()
        if last and last.employee_code.startswith('EMP-'):
            next_num = int(last.employee_code[4:]) + 1
        else:
            next_num = 1
        self.employee_code = f'EMP-{next_num:04d}'
    super().save(*args, **kwargs)
```

---

## Subscription Gate Integration

Add the following entries to `SubscriptionAccessMiddleware.FEATURE_RULES` in `subscription/middleware.py`:

```python
# HR payroll (PRO only) — must come before the basic HR rule
('/api/hr/payroll-runs/', 'hr_payroll'),
('/api/hr/payslips/', 'hr_payroll'),
# HR basic (MID+)
('/api/hr/', 'hr_basic'),
```

Add `hr_basic` and `hr_payroll` feature codes to the subscription plan feature matrix in `subscription/services.py`. FREE plan: neither. MID plan: `hr_basic`. PRO plan: `hr_basic` + `hr_payroll`.

---

## RBAC Permission Design

A custom DRF permission class `HRPermission` in `hr/permissions.py` enforces role-based access:

```python
class HRPermission(BasePermission):
    """
    admin   → full CRUD on all HR resources
    manager → CRU on employees, attendance, leave applications; no DELETE employee, no payroll
    accountant → read-only on all HR; can initiate/finalise payroll runs
    salesman → HTTP 403 on all /api/hr/ endpoints
    """
    SALESMAN_BLOCKED = True

    def has_permission(self, request, view):
        role = getattr(request.user, 'role', 'salesman')
        if role == 'salesman':
            return False
        if role == 'admin':
            return True
        # manager and accountant: read always allowed
        if request.method in SAFE_METHODS:
            return True
        # accountant: only payroll run actions
        if role == 'accountant':
            return getattr(view, 'payroll_action', False)
        # manager: CRU on employee/attendance/leave, no payroll
        if role == 'manager':
            return not getattr(view, 'payroll_action', False)
        return False

    def has_object_permission(self, request, view, obj):
        role = getattr(request.user, 'role', 'salesman')
        if role == 'admin':
            return True
        if role == 'manager' and request.method == 'DELETE':
            # managers cannot delete Employee records
            from hr.models import Employee
            if isinstance(obj, Employee):
                return False
        return True
```

ViewSets set `payroll_action = True` on `PayrollRunViewSet` and `PayslipViewSet`.


---

## API Endpoint Design

All endpoints are under `/api/hr/`. All ViewSets use `HRPermission` and filter querysets by `request.user.active_tenant`.

### URL Patterns (`hr/urls.py`)

```python
router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='department')
router.register(r'designations', DesignationViewSet, basename='designation')
router.register(r'employees', EmployeeViewSet, basename='employee')
router.register(r'attendance', AttendanceViewSet, basename='attendance')
router.register(r'leave-types', LeaveTypeViewSet, basename='leave-type')
router.register(r'leave-balances', LeaveBalanceViewSet, basename='leave-balance')
router.register(r'leave-applications', LeaveApplicationViewSet, basename='leave-application')
router.register(r'salary-structures', SalaryStructureViewSet, basename='salary-structure')
router.register(r'salary-assignments', SalaryAssignmentViewSet, basename='salary-assignment')
router.register(r'payroll-runs', PayrollRunViewSet, basename='payroll-run')
router.register(r'payslips', PayslipViewSet, basename='payslip')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', HRDashboardView.as_view(), name='hr-dashboard'),
    path('attendance/bulk/', BulkAttendanceView.as_view(), name='attendance-bulk'),
    path('leave-applications/<uuid:pk>/approve/', LeaveApproveView.as_view()),
    path('leave-applications/<uuid:pk>/reject/', LeaveRejectView.as_view()),
    path('payroll-runs/<uuid:pk>/run/', PayrollRunActionView.as_view()),
    path('payroll-runs/<uuid:pk>/finalise/', PayrollFinaliseView.as_view()),
    path('payslips/<uuid:pk>/pdf/', PayslipPDFView.as_view(), name='payslip-pdf'),
]
```

### Key ViewSet Behaviours

**EmployeeViewSet**
- `get_queryset()`: filters by `tenant=request.user.active_tenant`, excludes inactive for payroll-related nested lookups
- `perform_create()`: validates `user` FK belongs to same tenant; calls `audit_service.log_create()`
- `perform_update()`: captures before-state, calls `audit_service.log_update(before, after)`
- `perform_destroy()`: calls `audit_service.log_delete()`

**AttendanceViewSet**
- `update_or_create` logic: uses `AttendanceRecord.objects.update_or_create(employee=emp, date=date, defaults={'status': status})`

**BulkAttendanceView**
- Accepts `[{employee_id, date, status}, ...]` list; processes each as upsert inside a single transaction

**LeaveApplicationViewSet**
- `perform_create()`: computes `computed_days` via `leave_service.compute_leave_days(start, end, employee)`
- Approve action: updates status, creates/overwrites attendance records, decrements `LeaveBalance`
- Reject action: updates status only

**PayrollRunViewSet** (`payroll_action = True`)
- `run` action: validates no finalised run exists for month/year; dispatches `run_payroll_task.delay(run_id)`
- `finalise` action: sets status to `Finalised`, records `finalised_at`, writes audit log

**PayslipPDFView**
- Fetches `Payslip` scoped to tenant; calls `pdf_service.generate_payslip_pdf(payslip)`; returns `HttpResponse` with `Content-Type: application/pdf`; writes `DOWNLOAD` audit log entry

**HRDashboardView**
- Cache key: `hr_dashboard_{tenant_id}`; TTL: 300 seconds
- Computes: active employee count, employees on leave today, employees present today, net payroll of last finalised run


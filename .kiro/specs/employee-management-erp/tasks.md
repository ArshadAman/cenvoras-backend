# Implementation Plan: Employee Management ERP (`hr` app)

## Overview

Build the `hr` Django app inside the existing Cenvoras project. The implementation follows a foundation-first order: app scaffolding and models → subscription gate and RBAC → core HR APIs (departments, designations, employees) → attendance and leave → salary structures → payroll engine and statutory computations → payslip PDF → dashboard and caching. Every layer builds on the previous one; no code is left unintegrated.

Stack: Django 5.2.4 + DRF, PostgreSQL, Celery + Redis, reportlab.

---

## Tasks

- [x] 1. Scaffold the `hr` app and register it with the project
  - Create `hr/` directory with `__init__.py`, `apps.py`, `admin.py`, `models.py`, `serializers.py`, `views.py`, `urls.py`, `permissions.py`, `tasks.py`
  - Create `hr/services/__init__.py`, `hr/services/payroll_engine.py`, `hr/services/leave_service.py`, `hr/services/audit_service.py`, `hr/services/pdf_service.py` (empty stubs)
  - Create `hr/management/__init__.py`, `hr/management/commands/__init__.py`, `hr/management/commands/seed_pt_slabs.py` (empty stub)
  - Add `'hr'` to `INSTALLED_APPS` in `cenvoras/settings.py`
  - Add `path('api/hr/', include('hr.urls'))` to `cenvoras/urls.py`
  - _Requirements: 1.5, 2.1, 12.1_


- [x] 2. Define all HR data models and generate the initial migration
  - [x] 2.1 Implement `Department`, `Designation`, `Employee`, `AttendanceRecord` models in `hr/models.py`
    - Use `UUIDField` primary keys and `tenant` FK to `settings.AUTH_USER_MODEL` on every model
    - Add `unique_together = ('tenant', 'name')` on `Department` and `Designation`
    - Implement `Employee.save()` override for auto-generating `EMP-{NNNN}` employee codes scoped per tenant
    - Add `unique_together = ('employee', 'date')` on `AttendanceRecord`
    - _Requirements: 2.1, 2.2, 2.4, 3.1, 3.2, 4.1, 4.2, 12.1_

  - [x] 2.2 Implement `LeaveType`, `LeaveBalance`, `LeaveApplication` models in `hr/models.py`
    - Add `unique_together = ('employee', 'leave_type', 'year')` on `LeaveBalance`
    - Include `computed_days` and `lwp_days` `DecimalField`s on `LeaveApplication`
    - _Requirements: 5.1, 5.2, 6.1, 6.2_

  - [x] 2.3 Implement `SalaryStructure`, `SalaryComponent`, `EmployeeSalaryAssignment` models in `hr/models.py`
    - `SalaryComponent.is_basic` boolean; `component_type` choices: `fixed`, `pct_basic`, `pct_gross`
    - `EmployeeSalaryAssignment.computed_components` as `JSONField` for historical snapshot
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 2.4 Implement `PayrollRun`, `Payslip`, `ProfessionalTaxSlab` models in `hr/models.py`
    - `unique_together = ('tenant', 'month', 'year')` on `PayrollRun`
    - `unique_together = ('payroll_run', 'employee')` on `Payslip`
    - `ProfessionalTaxSlab` is global (no tenant FK); `upper_bound` nullable for top slab
    - _Requirements: 8.1, 8.8, 9.1_

  - [x] 2.5 Register all models in `hr/admin.py` and run `makemigrations hr` to produce `hr/migrations/0001_initial.py`
    - _Requirements: 2.1, 3.1, 4.1, 5.1, 7.1, 8.1, 9.1_


- [ ] 3. Integrate subscription gate and RBAC
  - [x] 3.1 Update `subscription/middleware.py` to add HR feature rules
    - Prepend `('/api/hr/payroll-runs/', 'hr_payroll')` and `('/api/hr/payslips/', 'hr_payroll')` before `('/api/hr/', 'hr_basic')` in `FEATURE_RULES`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 3.2 Update `subscription/services.py` to register `hr_basic` and `hr_payroll` feature codes
    - Add `'hr_basic'` and `'hr_payroll'` to `MODULE_FEATURES` dict
    - Update `can_use_feature()`: MID plan allows `hr_basic`; PRO/business plan allows both `hr_basic` and `hr_payroll`; FREE plan allows neither
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 3.3 Implement `HRPermission` DRF permission class in `hr/permissions.py`
    - `salesman` role → always `False`
    - `admin` role → always `True`
    - `manager` role → safe methods + CRU on employee/attendance/leave; no DELETE on `Employee`; no payroll actions
    - `accountant` role → safe methods + payroll actions only (views with `payroll_action = True`)
    - `has_object_permission`: block manager DELETE on `Employee` instances
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [-] 3.4 Write unit tests for `HRPermission` covering all four roles and edge cases
    - Test salesman blocked on all methods
    - Test manager blocked on DELETE Employee and payroll actions
    - Test accountant blocked on employee/attendance/leave mutations
    - _Requirements: 11.1, 11.2, 11.3, 11.4_


- [ ] 4. Implement `audit_service.py` helper
  - [x] 4.1 Write `hr/services/audit_service.py` with `log_create()`, `log_update()`, `log_delete()`, `log_download()` functions
    - Each function creates an `AuditLog` entry with `tenant`, `user`, `user_email`, `action`, `model_name`, `object_id`, `object_repr`, `changes`, and `ip_address` populated from the request
    - `log_update()` accepts `before` and `after` dicts and stores them in `changes`
    - `log_download()` uses action `'DOWNLOAD'` — add `'DOWNLOAD'` to `AuditLog.ACTION_CHOICES` in `audit_log/models.py`
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [-] 4.2 Write unit tests for `audit_service` functions
    - Verify `AuditLog` records are created with correct fields for each action type
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [~] 5. Checkpoint — Ensure models, migrations, subscription gate, RBAC, and audit service are all wired up
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 6. Implement Department and Designation APIs
  - [x] 6.1 Write `DepartmentSerializer` and `DesignationSerializer` in `hr/serializers.py`
    - Read-only `id`, `created_at`; writable `name`
    - _Requirements: 3.1, 3.2_

  - [x] 6.2 Implement `DepartmentViewSet` and `DesignationViewSet` in `hr/views.py`
    - `get_queryset()` filters by `tenant=request.user.active_tenant`
    - `perform_create()` sets `tenant` and calls `audit_service.log_create()`
    - `perform_update()` captures before-state and calls `audit_service.log_update()`
    - `perform_destroy()` blocks deletion if active employees are assigned (HTTP 400); calls `audit_service.log_delete()`
    - Apply `HRPermission` and `IsAuthenticated`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 12.1, 12.2, 12.3, 13.1_

  - [x] 6.3 Register `DepartmentViewSet` and `DesignationViewSet` in `hr/urls.py` via `DefaultRouter`
    - _Requirements: 3.1, 3.2_

  - [~] 6.4 Write unit tests for Department and Designation ViewSets
    - Test unique-name constraint per tenant
    - Test deletion blocked when active employees exist
    - Test cross-tenant isolation (HTTP 404 for foreign tenant IDs)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 12.2_


- [ ] 7. Implement Employee Profile API
  - [x] 7.1 Write `EmployeeSerializer` in `hr/serializers.py`
    - Include all mandatory and optional fields from the `Employee` model
    - Make `employee_code` read-only (auto-generated)
    - Validate that `user` FK belongs to the same tenant when provided
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 7.2 Implement `EmployeeViewSet` in `hr/views.py`
    - `get_queryset()` filters by `tenant=request.user.active_tenant`
    - `perform_create()`: validates `user` FK tenant membership; calls `audit_service.log_create()`
    - `perform_update()`: captures before-state; calls `audit_service.log_update()`
    - `perform_destroy()`: calls `audit_service.log_delete()`
    - Inactive employees excluded from payroll-related nested lookups (add `status` filter support)
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 12.1, 12.2, 12.3, 13.1_

  - [x] 7.3 Register `EmployeeViewSet` in `hr/urls.py`
    - _Requirements: 2.1_

  - [~] 7.4 Write unit tests for `EmployeeViewSet`
    - Test auto-generated `EMP-{NNNN}` codes are sequential and tenant-scoped
    - Test cross-tenant `user` FK validation returns HTTP 400
    - Test inactive employee excluded from active list
    - Test cross-tenant isolation returns HTTP 404
    - _Requirements: 2.3, 2.4, 2.5, 2.7, 12.2_


- [ ] 8. Implement Attendance Tracking API
  - [x] 8.1 Write `AttendanceRecordSerializer` in `hr/serializers.py`
    - Validate `employee` belongs to the active tenant
    - _Requirements: 4.1, 4.2_

  - [x] 8.2 Implement `AttendanceViewSet` in `hr/views.py` with upsert logic
    - `create()` / `update()` use `update_or_create(employee=emp, date=date, defaults={'status': status})` to satisfy the unique constraint
    - `get_queryset()` filters by tenant
    - Calls `audit_service.log_create()` or `audit_service.log_update()` accordingly
    - _Requirements: 4.1, 4.2, 4.3, 4.7, 12.1, 13.1_

  - [x] 8.3 Implement `BulkAttendanceView` as an `APIView` in `hr/views.py`
    - Accepts `[{employee_id, date, status}, ...]`; processes each as upsert inside a single `transaction.atomic()` block
    - Apply `HRPermission` and `IsAuthenticated`
    - _Requirements: 4.4_

  - [x] 8.4 Register `AttendanceViewSet` and wire `BulkAttendanceView` to `attendance/bulk/` in `hr/urls.py`
    - _Requirements: 4.1, 4.4_

  - [~] 8.5 Write unit tests for attendance upsert and bulk endpoint
    - Test duplicate (employee, date) results in update, not duplicate record
    - Test bulk endpoint processes all records in one transaction
    - _Requirements: 4.2, 4.3, 4.4_


- [ ] 9. Implement Leave Service, Leave Type, and Leave Application APIs
  - [x] 9.1 Write `hr/services/leave_service.py`
    - `compute_leave_days(start_date, end_date, employee)`: counts calendar days inclusive, excluding Sundays and days already marked `Holiday` in `AttendanceRecord` for that employee
    - `get_or_init_leave_balance(employee, leave_type, year)`: returns existing `LeaveBalance` or creates one initialised to `leave_type.annual_entitlement`
    - _Requirements: 5.2, 6.2_

  - [x] 9.2 Write `LeaveTypeSerializer` and `LeaveBalanceSerializer` in `hr/serializers.py`
    - _Requirements: 5.1, 5.2_

  - [x] 9.3 Implement `LeaveTypeViewSet` and `LeaveBalanceViewSet` in `hr/views.py`
    - `LeaveTypeViewSet.perform_destroy()`: block deletion if any `LeaveApplication` references the leave type (HTTP 400); call `audit_service.log_delete()`
    - `LeaveBalanceViewSet`: read-only for managers; admin can adjust balances
    - _Requirements: 5.1, 5.2, 5.5, 13.1_

  - [x] 9.4 Write `LeaveApplicationSerializer` in `hr/serializers.py`
    - _Requirements: 6.1_

  - [x] 9.5 Implement `LeaveApplicationViewSet` in `hr/views.py`
    - `perform_create()`: calls `leave_service.compute_leave_days()` to set `computed_days`; sets `status='pending'`; calls `audit_service.log_create()`
    - Calls `audit_service.log_update()` on any status change
    - _Requirements: 6.1, 6.2, 6.3, 6.6, 13.1_

  - [x] 9.6 Implement `LeaveApproveView` and `LeaveRejectView` as `APIView`s in `hr/views.py`
    - Approve: sets `status='approved'`; calls `leave_service.get_or_init_leave_balance()`; decrements balance; marks excess as `lwp_days`; creates/overwrites `AttendanceRecord` with `status='leave'` for each day in range (overwriting `absent` records); calls `audit_service.log_update()`
    - Reject: sets `status='rejected'`; does not touch attendance; calls `audit_service.log_update()`
    - _Requirements: 5.3, 5.4, 6.4, 6.5, 6.6_

  - [x] 9.7 Register all leave ViewSets and wire approve/reject paths in `hr/urls.py`
    - _Requirements: 5.1, 6.1_

  - [~] 9.8 Write unit tests for leave service and leave application lifecycle
    - Test `compute_leave_days` excludes Sundays and Holiday attendance records
    - Test approval decrements balance and creates attendance records
    - Test excess days beyond balance are marked as LWP
    - Test rejection leaves attendance unchanged
    - _Requirements: 5.3, 5.4, 6.2, 6.4, 6.5_

- [~] 10. Checkpoint — Ensure all HR profile, attendance, and leave tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 11. Implement Salary Structure and Assignment APIs
  - [x] 11.1 Write `SalaryStructureSerializer`, `SalaryComponentSerializer`, and `EmployeeSalaryAssignmentSerializer` in `hr/serializers.py`
    - Validate that each `SalaryStructure` has exactly one component with `is_basic=True`
    - On assignment creation, compute INR values for `pct_basic` and `pct_gross` components using `monthly_ctc` and store snapshot in `computed_components` JSON field
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 11.2 Implement `SalaryStructureViewSet` and `SalaryAssignmentViewSet` in `hr/views.py`
    - `get_queryset()` filters by tenant
    - `perform_create()` / `perform_update()` call `audit_service` helpers
    - `SalaryAssignmentViewSet` orders by `-effective_from` to support historical lookups
    - _Requirements: 7.1, 7.4, 7.6, 12.1, 13.1_

  - [x] 11.3 Register both ViewSets in `hr/urls.py`
    - _Requirements: 7.1, 7.4_

  - [~] 11.4 Write unit tests for salary structure validation and assignment computation
    - Test that creating a structure without a `Basic` component returns HTTP 400
    - Test that `computed_components` snapshot is stored correctly on assignment
    - Test that historical assignments are preserved when a new assignment is created
    - _Requirements: 7.3, 7.5, 7.6_


- [ ] 12. Implement Professional Tax slabs and seed command
  - [x] 12.1 Write `hr/management/commands/seed_pt_slabs.py`
    - Implement `handle()` to upsert `ProfessionalTaxSlab` records for all 8 states: Maharashtra, Karnataka, West Bengal, Tamil Nadu, Andhra Pradesh, Telangana, Gujarat, and Madhya Pradesh
    - Use `update_or_create` keyed on `(state_name, lower_bound)` so the command is idempotent
    - _Requirements: 9.1, 9.2_

  - [~] 12.2 Write a unit test that runs `seed_pt_slabs` and verifies all 8 states are present with correct slab data
    - _Requirements: 9.2_

- [ ] 13. Implement the Payroll Engine service
  - [x] 13.1 Write `hr/services/payroll_engine.py` — attendance and proration helpers
    - `get_present_days(employee, month, year)`: queries `AttendanceRecord`; counts `present` as 1.0, `half_day` as 0.5, `leave` (paid) as 1.0; treats missing days as absent (0)
    - `get_total_working_days(month, year)`: returns calendar working days (Mon–Sat) in the month
    - _Requirements: 4.5, 4.6, 8.1_

  - [x] 13.2 Write gross salary computation in `payroll_engine.py`
    - `compute_gross(employee, month, year)`: fetches the active `EmployeeSalaryAssignment` effective on the first day of the month; uses `computed_components` snapshot; prorates by `(present_days + paid_leave_days) / total_working_days`
    - _Requirements: 8.1_

  - [x] 13.3 Write PF computation in `payroll_engine.py`
    - `compute_pf(basic_salary)`: returns `{'employee_pf': 12% of basic, 'employer_pf': 12% of basic, 'employer_epf': 3.67% of basic, 'employer_eps': 8.33% of basic}`
    - _Requirements: 8.2_

  - [x] 13.4 Write ESI computation in `payroll_engine.py`
    - `compute_esi(gross_salary)`: if gross ≤ 21000, returns `{'employee_esi': 0.75% of gross, 'employer_esi': 3.25% of gross}`; else returns zeros
    - _Requirements: 8.3, 8.4_

  - [x] 13.5 Write TDS computation in `payroll_engine.py`
    - `compute_tds(gross_salary)`: annualises monthly gross; applies Indian income tax slabs for the current financial year (old regime); divides annual tax by 12; returns monthly TDS amount
    - _Requirements: 8.5_

  - [x] 13.6 Write Professional Tax lookup in `payroll_engine.py`
    - `compute_pt(gross_salary, work_state)`: queries `ProfessionalTaxSlab` for the state where `lower_bound ≤ gross_salary` and (`upper_bound` is null or `upper_bound ≥ gross_salary`); returns PT amount or 0 if no slab found
    - _Requirements: 8.6, 9.3, 9.4_

  - [x] 13.7 Write `compute_payslip_for_employee(employee, payroll_run)` in `payroll_engine.py`
    - Orchestrates calls to all computation helpers; calculates `net_salary = gross − employee_pf − employee_esi − tds − pt`; creates and returns a `Payslip` instance (not yet saved)
    - _Requirements: 8.7, 8.8_

  - [x] 13.8 Write `run_payroll(payroll_run_id)` in `payroll_engine.py`
    - Fetches all active employees for the tenant; calls `compute_payslip_for_employee()` for each; bulk-creates `Payslip` records; updates `PayrollRun.total_gross` and `PayrollRun.total_net`; all inside `transaction.atomic()`
    - _Requirements: 8.1, 8.8, 8.9_

  - [~] 13.9 Write unit tests for each payroll engine computation function
    - Test PF: 12% employee, 3.67% EPF + 8.33% EPS employer split
    - Test ESI: applied when gross ≤ 21000, not applied when gross > 21000
    - Test TDS: annualised slab calculation produces correct monthly deduction
    - Test PT: correct slab selected for each state; zero returned when no slab found
    - Test net salary formula: gross − employee_pf − employee_esi − tds − pt
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.3, 9.4_


- [ ] 14. Implement Celery task and Payroll Run API
  - [x] 14.1 Write `run_payroll_task` Celery task in `hr/tasks.py`
    - `@shared_task` that accepts `payroll_run_id`; calls `payroll_engine.run_payroll(payroll_run_id)`; handles exceptions and updates `PayrollRun` status to `draft` with an error note on failure
    - _Requirements: 8.1, 8.8_

  - [x] 14.2 Write `PayrollRunSerializer` and `PayslipSerializer` in `hr/serializers.py`
    - `PayrollRunSerializer`: include `month`, `year`, `status`, `total_gross`, `total_net`, `finalised_at`
    - `PayslipSerializer`: include all earnings, deductions, employer contributions, and net salary fields
    - _Requirements: 8.8, 10.1_

  - [x] 14.3 Implement `PayrollRunViewSet` in `hr/views.py` with `payroll_action = True`
    - `run` action (`POST payroll-runs/{id}/run/`): validates no `Finalised` run exists for the same tenant/month/year (HTTP 400 with `payroll_already_finalised`); dispatches `run_payroll_task.delay(run.id)`; calls `audit_service.log_create()`
    - `finalise` action (`POST payroll-runs/{id}/finalise/`): sets `status='Finalised'`, records `finalised_at=now()`; calls `audit_service.log_update()`
    - `get_queryset()` filters by tenant
    - _Requirements: 8.9, 8.10, 12.1, 13.1_

  - [x] 14.4 Implement `PayslipViewSet` in `hr/views.py` with `payroll_action = True`
    - Read-only ViewSet; `get_queryset()` filters by tenant
    - _Requirements: 10.1, 12.1_

  - [x] 14.5 Wire `PayrollRunViewSet`, `PayslipViewSet`, and special action paths (`run/`, `finalise/`) in `hr/urls.py`
    - _Requirements: 8.1, 8.9, 10.1_

  - [~] 14.6 Write unit tests for payroll run lifecycle
    - Test that re-running a `Finalised` payroll returns HTTP 400 with `payroll_already_finalised`
    - Test that `finalise` sets `finalised_at` and writes audit log
    - _Requirements: 8.9, 8.10_

- [~] 15. Checkpoint — Ensure payroll engine, Celery task, and payroll run API tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 16. Implement Payslip PDF generation
  - [x] 16.1 Write `hr/services/pdf_service.py` — `generate_payslip_pdf(payslip)`
    - Use `reportlab` to render a PDF with: tenant `business_name`, employee `full_name` and `employee_code`, month/year, earnings table (component name → INR amount), deductions table (PF, ESI, TDS, PT), employer contributions (employer PF, ESI), and net salary
    - All monetary values formatted as `₹X,XXX.XX` (two decimal places)
    - Returns a `BytesIO` buffer
    - _Requirements: 10.2, 10.3_

  - [x] 16.2 Implement `PayslipPDFView` as an `APIView` in `hr/views.py`
    - Fetches `Payslip` scoped to tenant (HTTP 404 if not found or wrong tenant)
    - Calls `pdf_service.generate_payslip_pdf(payslip)`
    - Returns `HttpResponse` with `Content-Type: application/pdf` and `Content-Disposition: attachment; filename="payslip_{employee_code}_{month}_{year}.pdf"`
    - Calls `audit_service.log_download()` with `model_name='Payslip'`
    - _Requirements: 10.2, 10.3, 10.4, 12.2, 13.1_

  - [x] 16.3 Wire `PayslipPDFView` to `payslips/{pk}/pdf/` in `hr/urls.py`
    - _Requirements: 10.2_

  - [~] 16.4 Write a unit test for `PayslipPDFView`
    - Test that the response has `Content-Type: application/pdf`
    - Test that a `DOWNLOAD` audit log entry is created
    - Test that accessing a payslip from a different tenant returns HTTP 404
    - _Requirements: 10.4, 12.2_


- [ ] 17. Implement HR Dashboard summary API
  - [x] 17.1 Implement `HRDashboardView` as an `APIView` in `hr/views.py`
    - Compute in `Asia/Kolkata` timezone: `total_active_employees`, `on_leave_today` (attendance status `leave` for today), `present_today` (attendance status `present` or `half_day` for today), `last_payroll_net` (net total of most recently `Finalised` `PayrollRun`, or `null`)
    - Cache result under key `hr_dashboard_{tenant_id}` with TTL 300 seconds using `django.core.cache`
    - Apply `HRPermission` and `IsAuthenticated`
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [x] 17.2 Wire `HRDashboardView` to `dashboard/` in `hr/urls.py`
    - _Requirements: 14.1_

  - [~] 17.3 Write unit tests for the dashboard endpoint
    - Test correct counts for active employees, on-leave, and present using IST date
    - Test `last_payroll_net` is `null` when no finalised run exists
    - Test response is served from cache on second call (mock cache)
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [~] 18. Final checkpoint — Full integration and all tests passing
  - Ensure all tests pass, ask the user if questions arise.


---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for full traceability
- The dependency order is strict: models → subscription/RBAC → audit service → core HR APIs → leave → salary → payroll engine → PDF → dashboard
- The `seed_pt_slabs` management command must be run once after the initial migration (`python manage.py seed_pt_slabs`)
- The `DOWNLOAD` action must be added to `AuditLog.ACTION_CHOICES` in `audit_log/models.py` as part of task 4.1 — this requires a new migration for `audit_log`
- Celery workers must be running for async payroll runs; the `run_payroll_task` is dispatched via `.delay()` and the `PayrollRun` status transitions to `Finalised` only via the explicit `finalise` action
- All monetary arithmetic uses Python `Decimal` to avoid floating-point errors
- The `SubscriptionAccessMiddleware.FEATURE_RULES` tuple is order-sensitive: payroll-specific prefixes must appear before the generic `/api/hr/` prefix

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "2.4"] },
    { "id": 2, "tasks": ["2.5"] },
    { "id": 3, "tasks": ["3.1", "3.2", "3.3", "4.1"] },
    { "id": 4, "tasks": ["3.4", "4.2", "6.1", "6.2", "6.3"] },
    { "id": 5, "tasks": ["6.4", "7.1", "7.2", "7.3", "9.1"] },
    { "id": 6, "tasks": ["7.4", "8.1", "8.2", "8.3", "8.4", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7"] },
    { "id": 7, "tasks": ["8.5", "9.8", "11.1", "11.2", "11.3", "12.1"] },
    { "id": 8, "tasks": ["11.4", "12.2", "13.1", "13.2", "13.3", "13.4", "13.5", "13.6"] },
    { "id": 9, "tasks": ["13.7"] },
    { "id": 10, "tasks": ["13.8"] },
    { "id": 11, "tasks": ["13.9", "14.1", "14.2"] },
    { "id": 12, "tasks": ["14.3", "14.4", "14.5"] },
    { "id": 13, "tasks": ["14.6", "16.1"] },
    { "id": 14, "tasks": ["16.2", "16.3"] },
    { "id": 15, "tasks": ["16.4", "17.1", "17.2"] },
    { "id": 16, "tasks": ["17.3"] }
  ]
}
```

# Requirements Document

## Introduction

This document defines requirements for the Employee Management ERP module within the Cenvoras platform. The module provides a full-stack HR and payroll solution for Indian businesses, covering employee profiles, department and designation management, manual attendance tracking, leave management, Indian statutory payroll computation (PF, ESI, TDS, Professional Tax), salary slip generation, and RBAC-gated access. All data is scoped to the active tenant (`User.parent` FK pattern). The module is gated behind subscription tiers: FREE tenants have no access, MID tenants access basic HR (profiles, attendance, leave), and PRO tenants access the full payroll engine and statutory compliance features. Every mutating operation is recorded via the existing `audit_log` app.

---

## Glossary

- **Tenant**: The root `User` account (where `parent` is null) that owns all data. All HR records are scoped to a single tenant.
- **Employee**: An HR record representing a person employed by the tenant's business. May optionally be linked to a Cenvoras `User` account via an optional FK.
- **Department**: An organisational grouping of employees within a tenant (e.g., Sales, Accounts, Warehouse).
- **Designation**: A job title or role label assigned to an employee (e.g., Manager, Accountant, Driver).
- **Attendance Record**: A daily record of an employee's presence status (Present, Absent, Half-Day, Leave, Holiday) entered manually by an admin or manager.
- **Leave Type**: A configurable category of leave (e.g., Casual Leave, Sick Leave, Earned Leave) with an annual entitlement quota.
- **Leave Application**: A request for an employee to be absent for one or more days under a specific Leave Type, approved or rejected by an admin or manager.
- **Salary Structure**: A named template defining the fixed and variable components of an employee's compensation (Basic, HRA, Conveyance, Special Allowance, etc.).
- **Payroll Run**: The monthly computation process that calculates gross pay, statutory deductions, and net pay for all active employees in a given month and year.
- **Payslip**: The output document for a single employee for a single Payroll Run, showing all earnings, deductions, and net pay.
- **PF (Provident Fund)**: Statutory deduction under the Employees' Provident Funds Act. Employee contribution: 12% of Basic. Employer contribution: 12% of Basic (split: 3.67% to EPF, 8.33% to EPS).
- **ESI (Employees' State Insurance)**: Statutory deduction applicable when gross salary ≤ ₹21,000/month. Employee contribution: 0.75% of gross. Employer contribution: 3.25% of gross.
- **TDS (Tax Deducted at Source)**: Income tax deducted monthly from salary as per the applicable Indian income tax slab for the financial year.
- **Professional Tax (PT)**: State-level tax deducted from salary as per the slab defined for the employee's work state.
- **Gross Salary**: Total earnings before any deductions (sum of all salary components).
- **Net Salary**: Gross Salary minus all statutory and voluntary deductions.
- **RBAC**: Role-Based Access Control. Existing roles: `admin`, `manager`, `salesman`, `accountant`.
- **Audit Log**: The existing `AuditLog` model in the `audit_log` app that records CREATE, UPDATE, and DELETE actions with before/after state.
- **HR Module**: The collective set of features covering employee profiles, attendance, leave, and payroll within this spec.
- **MID Tier**: Subscription tier granting access to basic HR features (employee profiles, departments, designations, attendance, leave).
- **PRO Tier**: Subscription tier granting access to all HR features including the payroll engine, statutory calculations, and salary slips.

---

## Requirements

### Requirement 1: Subscription-Gated HR Module Access

**User Story:** As a tenant admin, I want HR features to be available only on MID and PRO subscription tiers, so that the module is correctly monetised and FREE-tier tenants cannot access HR data.

#### Acceptance Criteria

1. WHEN a user with a FREE-tier subscription sends a request to any `/api/hr/` endpoint, THE HR Module SHALL return HTTP 403 with error code `plan_locked`.
2. WHEN a user with a MID-tier subscription accesses employee profile, department, designation, attendance, or leave endpoints, THE HR Module SHALL permit the request.
3. WHEN a user with a MID-tier subscription accesses payroll, payslip, or statutory computation endpoints, THE HR Module SHALL return HTTP 403 with error code `feature_locked`.
4. WHEN a user with a PRO-tier subscription accesses any HR endpoint, THE HR Module SHALL permit the request.
5. THE HR Module SHALL integrate with the existing `SubscriptionAccessMiddleware` by registering `/api/hr/` route prefixes and their required feature codes (`hr_basic` for MID, `hr_payroll` for PRO).

---

### Requirement 2: Employee Profile Management

**User Story:** As an admin or manager, I want to create and maintain complete employee records, so that the business has a single source of truth for all HR data.

#### Acceptance Criteria

1. THE HR Module SHALL store the following mandatory fields per employee: full name, date of birth, date of joining, gender, employment type (Full-Time, Part-Time, Contract), department, designation, and work state (for Professional Tax slab selection).
2. THE HR Module SHALL store the following optional fields per employee: personal email, personal phone, PAN number, Aadhaar number, bank account number, bank IFSC code, bank name, UAN (Universal Account Number for PF), ESI IP number, and a nullable FK to `users.User`.
3. WHEN an employee record is created with a non-null `user` FK, THE HR Module SHALL verify that the linked `User` belongs to the same tenant before saving.
4. THE HR Module SHALL assign each employee a unique, auto-generated employee code in the format `EMP-{NNNN}` (zero-padded sequential number) scoped per tenant.
5. WHEN an employee's employment status is set to `Inactive`, THE HR Module SHALL exclude that employee from future Payroll Runs while retaining all historical records.
6. WHEN an employee record is created, updated, or deleted, THE HR Module SHALL write an entry to `AuditLog` with the action, model name `Employee`, object ID, and a JSON diff of changed fields.
7. THE HR Module SHALL scope all employee queries to the active tenant so that no employee record is visible across tenant boundaries.

---

### Requirement 3: Department and Designation Management

**User Story:** As an admin, I want to define departments and designations, so that employees can be organised into a meaningful hierarchy.

#### Acceptance Criteria

1. THE HR Module SHALL allow an admin to create, update, and delete Department records with a unique name per tenant.
2. THE HR Module SHALL allow an admin to create, update, and delete Designation records with a unique name per tenant.
3. IF a Department is deleted while one or more active employees are assigned to it, THEN THE HR Module SHALL return HTTP 400 with a descriptive error and SHALL NOT delete the Department.
4. IF a Designation is deleted while one or more active employees are assigned to it, THEN THE HR Module SHALL return HTTP 400 with a descriptive error and SHALL NOT delete the Designation.
5. WHEN a Department or Designation record is created, updated, or deleted, THE HR Module SHALL write an entry to `AuditLog`.

---

### Requirement 4: Attendance Tracking

**User Story:** As an admin or manager, I want to manually record daily attendance for each employee, so that payroll calculations reflect actual working days.

#### Acceptance Criteria

1. THE HR Module SHALL accept attendance entries with the following fields: employee, date, and status (Present, Absent, Half-Day, Leave, Holiday).
2. THE HR Module SHALL enforce a unique constraint on (employee, date) so that only one attendance record exists per employee per day.
3. WHEN an admin or manager submits attendance for a date that already has a record for that employee, THE HR Module SHALL update the existing record rather than creating a duplicate.
4. THE HR Module SHALL allow bulk attendance submission for a single date covering multiple employees in one API call.
5. WHEN a Payroll Run is initiated for a given month, THE HR Module SHALL compute the number of Present days (counting Half-Day as 0.5) for each employee from attendance records in that month.
6. IF attendance records are missing for some working days in a month, THEN THE HR Module SHALL treat those missing days as Absent for payroll computation purposes.
7. WHEN an attendance record is created or updated, THE HR Module SHALL write an entry to `AuditLog`.

---

### Requirement 5: Leave Type Configuration

**User Story:** As an admin, I want to configure leave types with annual quotas, so that leave balances can be tracked per employee.

#### Acceptance Criteria

1. THE HR Module SHALL allow an admin to create Leave Types with the following fields: name, annual entitlement (integer number of days), and whether the leave is paid or unpaid.
2. THE HR Module SHALL maintain a Leave Balance record per employee per Leave Type per calendar year, initialised to the annual entitlement on the first leave application or payroll run of that year.
3. WHEN a Leave Application is approved, THE HR Module SHALL decrement the employee's Leave Balance for the corresponding Leave Type by the number of approved leave days.
4. IF a Leave Application is approved and the employee's Leave Balance for that Leave Type is zero, THEN THE HR Module SHALL still approve the application but SHALL mark the excess days as Leave Without Pay (LWP).
5. WHEN a Leave Type is deleted, THE HR Module SHALL return HTTP 400 if any Leave Applications reference that Leave Type and SHALL NOT delete it.

---

### Requirement 6: Leave Application Management

**User Story:** As an admin or manager, I want to create and approve leave applications for employees, so that absences are formally recorded and reflected in payroll.

#### Acceptance Criteria

1. THE HR Module SHALL allow an admin or manager to create a Leave Application with the following fields: employee, leave type, start date, end date, and reason.
2. THE HR Module SHALL compute the number of leave days as the count of calendar days between start date and end date (inclusive) excluding Sundays and days already marked as Holiday in attendance records.
3. WHEN a Leave Application is created, THE HR Module SHALL set its status to `Pending`.
4. WHEN an admin or manager approves a Leave Application, THE HR Module SHALL set its status to `Approved` and SHALL create corresponding attendance records with status `Leave` for each day in the application's date range, overwriting any existing `Absent` records for those days.
5. WHEN an admin or manager rejects a Leave Application, THE HR Module SHALL set its status to `Rejected` and SHALL NOT modify attendance records.
6. WHEN a Leave Application is created, approved, or rejected, THE HR Module SHALL write an entry to `AuditLog`.

---

### Requirement 7: Salary Structure Definition

**User Story:** As an admin, I want to define salary structures with named components, so that payroll calculations are consistent and auditable.

#### Acceptance Criteria

1. THE HR Module SHALL allow an admin to create a Salary Structure with a name and one or more Salary Components.
2. THE HR Module SHALL support the following Salary Component types: fixed amount (INR), percentage of Basic, and percentage of Gross.
3. THE HR Module SHALL require that each Salary Structure contains exactly one component designated as `Basic`.
4. THE HR Module SHALL allow a Salary Structure to be assigned to one or more employees with an effective-from date and a monthly CTC (Cost to Company) amount.
5. WHEN a Salary Structure is assigned to an employee, THE HR Module SHALL compute the INR value of each percentage-based component using the assigned CTC as the basis.
6. THE HR Module SHALL retain historical salary structure assignments so that past Payroll Runs can be recomputed using the salary in effect at that time.

---

### Requirement 8: Indian Statutory Payroll Computation

**User Story:** As an admin, I want the payroll engine to automatically compute PF, ESI, TDS, and Professional Tax deductions, so that statutory compliance is accurate and requires no manual calculation.

#### Acceptance Criteria

1. WHEN a Payroll Run is initiated for a given month and year, THE Payroll Engine SHALL compute gross salary for each active employee as the sum of all salary components in the employee's active Salary Structure, prorated by (Present days + approved paid Leave days) / total working days in that month.
2. WHEN computing PF for an employee, THE Payroll Engine SHALL deduct 12% of the employee's Basic salary as the employee PF contribution and SHALL record 12% of Basic as the employer PF contribution (split: 3.67% EPF + 8.33% EPS).
3. WHILE an employee's computed gross salary for the month is less than or equal to ₹21,000, THE Payroll Engine SHALL deduct 0.75% of gross as the employee ESI contribution and SHALL record 3.25% of gross as the employer ESI contribution.
4. WHILE an employee's computed gross salary for the month exceeds ₹21,000, THE Payroll Engine SHALL NOT apply ESI deductions for that employee in that month.
5. WHEN computing TDS for an employee, THE Payroll Engine SHALL annualise the monthly gross salary, apply the applicable Indian income tax slab for the current financial year, divide the annual tax liability by 12, and deduct the result as monthly TDS.
6. WHEN computing Professional Tax for an employee, THE Payroll Engine SHALL apply the PT slab defined for the employee's work state to the monthly gross salary and deduct the resulting PT amount.
7. THE Payroll Engine SHALL compute Net Salary as: Gross Salary − Employee PF − Employee ESI − TDS − Professional Tax.
8. WHEN a Payroll Run is completed, THE Payroll Engine SHALL create one Payslip record per employee containing gross salary, each earnings component, each deduction component, employer contributions, and net salary.
9. IF a Payroll Run already exists for a given tenant, month, and year with status `Finalised`, THEN THE Payroll Engine SHALL reject a re-run request with HTTP 400 and error code `payroll_already_finalised`.
10. WHEN a Payroll Run is created or finalised, THE HR Module SHALL write an entry to `AuditLog`.

---

### Requirement 9: Professional Tax State Slabs

**User Story:** As an admin, I want the system to maintain state-wise Professional Tax slabs, so that PT deductions are correct for employees working in different Indian states.

#### Acceptance Criteria

1. THE HR Module SHALL store Professional Tax slabs as configurable records with fields: state name, monthly salary lower bound (INR), monthly salary upper bound (INR, nullable for the top slab), and PT amount (INR).
2. THE HR Module SHALL seed default PT slabs for the following states at application startup: Maharashtra, Karnataka, West Bengal, Tamil Nadu, Andhra Pradesh, Telangana, Gujarat, and Madhya Pradesh.
3. WHEN the Payroll Engine looks up PT for an employee, THE Payroll Engine SHALL select the PT slab where the employee's gross salary falls within the lower and upper bounds for the employee's work state.
4. IF no PT slab is found for an employee's work state, THEN THE Payroll Engine SHALL apply a PT deduction of ₹0 for that employee.

---

### Requirement 10: Salary Slip Generation and Download

**User Story:** As an admin or manager, I want to generate and download salary slips for employees, so that employees can receive formal proof of their monthly compensation.

#### Acceptance Criteria

1. WHEN an admin or manager requests a salary slip for a specific employee and Payroll Run, THE HR Module SHALL return a structured JSON payload containing all earnings, deductions, employer contributions, and net pay.
2. THE HR Module SHALL expose a PDF download endpoint for each Payslip that renders the salary slip with the tenant's business name, employee name, employee code, month/year, all earnings components, all deduction components, employer PF and ESI contributions, and net salary.
3. THE HR Module SHALL display all monetary values in INR (₹) with two decimal places on the salary slip PDF.
4. WHEN a Payslip PDF is downloaded, THE HR Module SHALL write an entry to `AuditLog` with action `DOWNLOAD` and model name `Payslip`.

---

### Requirement 11: RBAC Enforcement for HR Operations

**User Story:** As a tenant admin, I want HR operations to be restricted by role, so that sensitive payroll and employee data is accessible only to authorised users.

#### Acceptance Criteria

1. THE HR Module SHALL permit users with role `admin` to perform all create, read, update, and delete operations on all HR resources.
2. THE HR Module SHALL permit users with role `manager` to create, read, and update employee profiles, attendance records, and leave applications, but SHALL NOT permit managers to delete employee records or initiate Payroll Runs.
3. THE HR Module SHALL permit users with role `accountant` to read all HR resources and to initiate and finalise Payroll Runs, but SHALL NOT permit accountants to create, update, or delete employee profiles, attendance records, or leave applications.
4. THE HR Module SHALL NOT permit users with role `salesman` to access any HR endpoint, returning HTTP 403 for all `/api/hr/` requests made by a salesman.
5. WHEN a user attempts an operation they are not authorised to perform, THE HR Module SHALL return HTTP 403 with a descriptive error message identifying the required role.

---

### Requirement 12: Tenant Data Isolation

**User Story:** As a tenant admin, I want all HR data to be strictly isolated to my tenant, so that no data leaks across business accounts.

#### Acceptance Criteria

1. THE HR Module SHALL filter all Employee, Department, Designation, Attendance Record, Leave Application, Salary Structure, Payroll Run, and Payslip queries by the active tenant derived from `request.user.active_tenant`.
2. WHEN a user attempts to access an HR resource belonging to a different tenant by supplying a valid UUID, THE HR Module SHALL return HTTP 404.
3. THE HR Module SHALL enforce tenant scoping at the queryset level in all DRF ViewSets so that tenant isolation cannot be bypassed by manipulating request parameters.

---

### Requirement 13: Audit Trail for All HR Mutations

**User Story:** As a tenant admin, I want every HR data change to be logged in the audit trail, so that I can review who changed what and when.

#### Acceptance Criteria

1. THE HR Module SHALL record an `AuditLog` entry for every CREATE, UPDATE, and DELETE operation on the following models: Employee, Department, Designation, Attendance Record, Leave Type, Leave Application, Salary Structure, Payroll Run, and Payslip.
2. WHEN recording an UPDATE audit entry, THE HR Module SHALL store the before and after state of changed fields in the `changes` JSON field of `AuditLog`.
3. THE HR Module SHALL populate the `tenant` FK on every `AuditLog` entry with the active tenant of the requesting user.
4. THE HR Module SHALL populate the `ip_address` field on every `AuditLog` entry using the IP address from the incoming request.

---

### Requirement 14: HR Dashboard Summary API

**User Story:** As an admin or manager, I want a summary API endpoint that returns key HR metrics, so that the frontend dashboard can display headcount, attendance, and payroll totals at a glance.

#### Acceptance Criteria

1. THE HR Module SHALL expose a read-only summary endpoint at `/api/hr/dashboard/` that returns the following metrics for the active tenant: total active employee count, total employees on leave today, total employees present today, and the net payroll amount for the most recently finalised Payroll Run.
2. WHEN the summary endpoint is called, THE HR Module SHALL compute today's attendance metrics using attendance records where date equals the current date in the `Asia/Kolkata` timezone.
3. IF no Payroll Run has been finalised for the tenant, THEN THE HR Module SHALL return `null` for the net payroll amount field.
4. THE HR Module SHALL cache the dashboard summary response for 5 minutes per tenant using the existing Redis cache infrastructure.

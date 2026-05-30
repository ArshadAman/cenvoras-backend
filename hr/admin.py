from django.contrib import admin

from hr.models import (
    AttendanceRecord,
    Department,
    Designation,
    Employee,
    EmployeeSalaryAssignment,
    LeaveApplication,
    LeaveBalance,
    LeaveType,
    PayrollRun,
    Payslip,
    ProfessionalTaxSlab,
    SalaryComponent,
    SalaryStructure,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'created_at')
    list_filter = ('tenant',)
    search_fields = ('name',)
    ordering = ('tenant', 'name')


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'created_at')
    list_filter = ('tenant',)
    search_fields = ('name',)
    ordering = ('tenant', 'name')


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'employee_code', 'full_name', 'department', 'designation',
        'employment_type', 'status', 'tenant', 'created_at',
    )
    list_filter = ('status', 'employment_type', 'gender', 'tenant')
    search_fields = ('employee_code', 'full_name', 'pan_number', 'aadhaar_number')
    ordering = ('tenant', 'employee_code')
    readonly_fields = ('employee_code', 'created_at', 'updated_at')


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'status', 'tenant', 'created_at')
    list_filter = ('status', 'tenant', 'date')
    search_fields = ('employee__full_name', 'employee__employee_code')
    ordering = ('-date', 'employee')
    date_hierarchy = 'date'


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'annual_entitlement', 'is_paid', 'tenant', 'created_at')
    list_filter = ('is_paid', 'tenant')
    search_fields = ('name',)
    ordering = ('tenant', 'name')


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'year', 'balance')
    list_filter = ('year', 'leave_type')
    search_fields = ('employee__full_name', 'employee__employee_code')
    ordering = ('employee', 'leave_type', '-year')


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'leave_type', 'start_date', 'end_date',
        'computed_days', 'lwp_days', 'status', 'tenant', 'applied_at',
    )
    list_filter = ('status', 'leave_type', 'tenant')
    search_fields = ('employee__full_name', 'employee__employee_code')
    ordering = ('-applied_at',)
    readonly_fields = ('computed_days', 'lwp_days', 'applied_at', 'updated_at')


@admin.register(SalaryStructure)
class SalaryStructureAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'created_at')
    list_filter = ('tenant',)
    search_fields = ('name',)
    ordering = ('tenant', 'name')


@admin.register(SalaryComponent)
class SalaryComponentAdmin(admin.ModelAdmin):
    list_display = ('name', 'salary_structure', 'component_type', 'is_basic', 'value')
    list_filter = ('component_type', 'is_basic')
    search_fields = ('name', 'salary_structure__name')
    ordering = ('salary_structure', 'name')


@admin.register(EmployeeSalaryAssignment)
class EmployeeSalaryAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'salary_structure', 'effective_from', 'monthly_ctc', 'tenant',
    )
    list_filter = ('tenant', 'salary_structure')
    search_fields = ('employee__full_name', 'employee__employee_code')
    ordering = ('-effective_from',)
    readonly_fields = ('computed_components', 'created_at', 'updated_at')


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = (
        'month', 'year', 'status', 'total_gross', 'total_net',
        'tenant', 'created_at', 'finalised_at',
    )
    list_filter = ('status', 'tenant', 'year')
    search_fields = ('tenant__email',)
    ordering = ('-year', '-month')
    readonly_fields = ('total_gross', 'total_net', 'finalised_at', 'created_at', 'updated_at')


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'payroll_run', 'gross_salary', 'net_salary',
        'present_days', 'total_working_days', 'tenant',
    )
    list_filter = ('tenant', 'payroll_run__year', 'payroll_run__month')
    search_fields = ('employee__full_name', 'employee__employee_code')
    ordering = ('payroll_run', 'employee__employee_code')
    readonly_fields = ('earnings', 'deductions', 'created_at')


@admin.register(ProfessionalTaxSlab)
class ProfessionalTaxSlabAdmin(admin.ModelAdmin):
    list_display = ('state_name', 'lower_bound', 'upper_bound', 'pt_amount')
    list_filter = ('state_name',)
    search_fields = ('state_name',)
    ordering = ('state_name', 'lower_bound')

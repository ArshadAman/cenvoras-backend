# HR DRF serializers — fully enhanced for Payroll-Centric HRMS
from decimal import Decimal
from rest_framework import serializers

from .models import (
    Department, Designation, Employee, AttendanceRecord,
    LeaveType, LeaveBalance, LeaveApplication,
    SalaryStructure, SalaryComponent, EmployeeSalaryAssignment,
    PayrollRun, Payslip, EmployeeTask, EmployeeQuery, EmployeeNotification,
    EmployeeSalaryHistory, OvertimeRecord, EmployeeAdvanceLoan, LoanRecoveryLog,
    PayrollException, HRDocument, HRMSSettings
)


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_name(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            qs = Department.objects.filter(tenant=tenant, name__iexact=value)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError("A department with this name already exists.")
        return value


class DesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Designation
        fields = ['id', 'name', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_name(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            qs = Designation.objects.filter(tenant=tenant, name__iexact=value)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError("A designation with this name already exists.")
        return value


class EmployeeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    designation_name = serializers.CharField(source='designation.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True, default='')
    reporting_manager_name = serializers.CharField(source='reporting_manager.full_name', read_only=True, default='')
    current_ctc = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        exclude = ['tenant']
        read_only_fields = ['id', 'employee_code', 'created_at', 'updated_at']

    def get_current_ctc(self, obj):
        latest = obj.salary_assignments.order_by('-effective_from').first()
        return str(latest.monthly_ctc) if latest else '0.00'

    def validate_user(self, value):
        if value:
            request = self.context.get('request')
            if request and hasattr(request, 'user'):
                tenant = getattr(request.user, 'active_tenant', request.user)
                user_tenant = getattr(value, 'active_tenant', value)
                if user_tenant != tenant and value != tenant:
                    raise serializers.ValidationError("Assigned user does not belong to the active tenant.")
        return value


class EmployeeSalaryHistorySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    structure_name = serializers.CharField(source='salary_structure.name', read_only=True, default='')
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')

    class Meta:
        model = EmployeeSalaryHistory
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at']


class AttendanceRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)

    class Meta:
        model = AttendanceRecord
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']
        validators = []


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            qs = LeaveType.objects.filter(tenant=tenant, name__iexact=value)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError("A leave type with this name already exists.")
        return value


class LeaveBalanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)

    class Meta:
        model = LeaveBalance
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class LeaveApplicationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)

    class Meta:
        model = LeaveApplication
        exclude = ['tenant']
        read_only_fields = ['id', 'computed_days', 'lwp_days', 'applied_at', 'updated_at', 'status']

    def validate(self, data):
        start = data.get('start_date')
        end = data.get('end_date')
        if start and end and start > end:
            raise serializers.ValidationError({"end_date": "End date must be on or after start date."})
        return data


class SalaryComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryComponent
        fields = [
            'id', 'name', 'type', 'component_type', 'calculation_base',
            'is_basic', 'value', 'percentage', 'is_taxable',
            'pf_applicable', 'esi_applicable', 'pt_applicable', 'tds_applicable',
            'employee_contribution_pct', 'employer_contribution_pct',
            'is_active', 'order'
        ]
        read_only_fields = ['id']


class SalaryStructureSerializer(serializers.ModelSerializer):
    components = SalaryComponentSerializer(many=True, required=False)

    class Meta:
        model = SalaryStructure
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            qs = SalaryStructure.objects.filter(tenant=tenant, name__iexact=value)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError("A salary structure with this name already exists.")
        return value

    def validate(self, data):
        components_data = self.initial_data.get('components', None)
        if components_data is not None:
            basics = [c for c in components_data if c.get('is_basic') is True]
            if len(basics) != 1:
                raise serializers.ValidationError("Exactly one component must be designated as the Basic component.")
        return data

    def create(self, validated_data):
        components_data = validated_data.pop('components', None)
        if components_data is None:
            components_data = self.initial_data.get('components', [])
        structure = SalaryStructure.objects.create(**validated_data)
        for comp in components_data:
            if isinstance(comp, dict):
                SalaryComponent.objects.create(salary_structure=structure, **comp)
        return structure

    def update(self, instance, validated_data):
        components_data = validated_data.pop('components', None)
        if components_data is None and 'components' in self.initial_data:
            components_data = self.initial_data.get('components', [])
        instance.name = validated_data.get('name', instance.name)
        instance.description = validated_data.get('description', instance.description)
        instance.save()

        if components_data is not None:
            instance.components.all().delete()
            for comp in components_data:
                if isinstance(comp, dict):
                    SalaryComponent.objects.create(salary_structure=instance, **comp)
        return instance


class EmployeeSalaryAssignmentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    salary_structure_name = serializers.CharField(source='salary_structure.name', read_only=True)

    class Meta:
        model = EmployeeSalaryAssignment
        exclude = ['tenant']
        read_only_fields = ['id', 'computed_components', 'created_at', 'updated_at']

    def validate_employee(self, value):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            if value.tenant != tenant:
                raise serializers.ValidationError("Employee does not belong to the active tenant.")
        return value

    def create(self, validated_data):
        assignment = super().create(validated_data)
        self._compute_components(assignment)
        return assignment

    def update(self, instance, validated_data):
        assignment = super().update(instance, validated_data)
        self._compute_components(assignment)
        return assignment

    def _compute_components(self, assignment):
        ctc = Decimal(str(assignment.monthly_ctc))
        components = assignment.salary_structure.components.all()

        basic_comp = next((c for c in components if c.is_basic), None)
        if not basic_comp:
            raise serializers.ValidationError("Salary structure must have a basic component.")

        computed = {}
        if basic_comp.component_type == 'fixed':
            basic_value = Decimal(str(basic_comp.value))
        else:
            basic_value = ctc * (Decimal(str(basic_comp.value)) / Decimal('100.0'))

        for comp in components:
            if comp.component_type == 'fixed':
                val = Decimal(str(comp.value))
            elif comp.component_type == 'pct_gross' or comp.component_type == 'pct_ctc':
                val = ctc * (Decimal(str(comp.value)) / Decimal('100.0'))
            elif comp.component_type == 'pct_basic':
                val = basic_value * (Decimal(str(comp.value)) / Decimal('100.0'))
            else:
                val = Decimal('0.0')

            computed[comp.name] = str(round(val, 2))

        assignment.computed_components = computed
        assignment.save(update_fields=['computed_components'])


class OvertimeRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')

    class Meta:
        model = OvertimeRecord
        exclude = ['tenant']
        read_only_fields = ['id', 'amount', 'created_at', 'updated_at', 'approved_by']


class EmployeeAdvanceLoanSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    disbursed_by_name = serializers.CharField(source='disbursed_by.username', read_only=True, default='')

    class Meta:
        model = EmployeeAdvanceLoan
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at', 'disbursed_by']

    def create(self, validated_data):
        if not validated_data.get('outstanding_balance'):
            validated_data['outstanding_balance'] = validated_data['original_amount']
        return super().create(validated_data)


class LoanRecoveryLogSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='loan.employee.full_name', read_only=True)
    loan_type = serializers.CharField(source='loan.record_type', read_only=True)

    class Meta:
        model = LoanRecoveryLog
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at']


class PayrollExceptionSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True, default='General')
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True, default='')

    class Meta:
        model = PayrollException
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at']


class PayrollRunSerializer(serializers.ModelSerializer):
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True, default='')
    paid_by_name = serializers.CharField(source='paid_by.username', read_only=True, default='')
    locked_by_name = serializers.CharField(source='locked_by.username', read_only=True, default='')
    payment_account_name = serializers.CharField(source='payment_account.name', read_only=True, default='')
    critical_exceptions_count = serializers.SerializerMethodField()
    warning_exceptions_count = serializers.SerializerMethodField()
    employee_count = serializers.SerializerMethodField()

    class Meta:
        model = PayrollRun
        exclude = ['tenant']
        read_only_fields = [
            'id', 'status', 'total_gross', 'total_deductions', 'total_net',
            'total_employer_contributions', 'approved_at', 'approved_by',
            'paid_at', 'paid_by', 'locked_at', 'locked_by', 'finalised_at',
            'is_reopened', 'reopened_at', 'reopened_by', 'created_at', 'updated_at'
        ]

    def get_critical_exceptions_count(self, obj):
        return obj.exceptions.filter(severity='critical', is_resolved=False).count()

    def get_warning_exceptions_count(self, obj):
        return obj.exceptions.filter(severity='warning', is_resolved=False).count()

    def get_employee_count(self, obj):
        return obj.payslips.count()

    def validate(self, data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            tenant = getattr(request.user, 'active_tenant', request.user)
            month = data.get('month', getattr(self.instance, 'month', None))
            year = data.get('year', getattr(self.instance, 'year', None))

            # Disallow duplicate payroll runs for the same period
            qs = PayrollRun.objects.filter(tenant=tenant, month=month, year=year)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError(f"A payroll run for month {month}/{year} already exists.")
        return data


class PayslipSerializer(serializers.ModelSerializer):
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    department_name = serializers.CharField(source='employee.department.name', read_only=True, default='')
    designation_name = serializers.CharField(source='employee.designation.name', read_only=True, default='')
    branch_name = serializers.CharField(source='employee.branch.name', read_only=True, default='')
    bank_name = serializers.CharField(source='employee.bank_name', read_only=True, default='')
    bank_account_number = serializers.CharField(source='employee.bank_account_number', read_only=True, default='')
    bank_ifsc = serializers.CharField(source='employee.bank_ifsc', read_only=True, default='')
    pan_number = serializers.CharField(source='employee.pan_number', read_only=True, default='')
    uan = serializers.CharField(source='employee.uan', read_only=True, default='')

    class Meta:
        model = Payslip
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at']


class HRDocumentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    uploaded_by_name = serializers.CharField(source='uploaded_by.username', read_only=True, default='')

    class Meta:
        model = HRDocument
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'uploaded_by']


class HRMSSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRMSSettings
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']


class EmployeeTaskSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    assigned_by_name = serializers.CharField(source='assigned_by.username', read_only=True)

    class Meta:
        model = EmployeeTask
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'assigned_by']


class EmployeeQuerySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    resolved_by_name = serializers.CharField(source='resolved_by.username', read_only=True)

    class Meta:
        model = EmployeeQuery
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'resolved_by']


class EmployeeNotificationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = EmployeeNotification
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'created_by', 'tenant']

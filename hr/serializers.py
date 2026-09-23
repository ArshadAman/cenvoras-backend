# HR DRF serializers — fully enhanced for Payroll-Centric HRMS
from decimal import Decimal
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Department, Designation, Employee, AttendanceRecord,
    LeaveType, LeaveBalance, LeaveApplication,
    SalaryStructure, SalaryComponent, EmployeeSalaryAssignment,
    PayrollRun, Payslip, EmployeeTask, EmployeeQuery, EmployeeNotification,
    EmployeeSalaryHistory, OvertimeRecord, EmployeeAdvanceLoan, LoanRecoveryLog,
    PayrollException, HRDocument, HRMSSettings, EmployeeAllowanceBonus,
    EmployeeTaxDeclaration
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
    salary_details = serializers.SerializerMethodField()

    personal_phone = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=15,
        error_messages={'required': 'Phone number is mandatory.', 'blank': 'Phone number is mandatory.'}
    )
    bank_name = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=100,
        error_messages={'required': 'Bank name is mandatory.', 'blank': 'Bank name is mandatory.'}
    )
    bank_account_number = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=20,
        error_messages={'required': 'Bank account number is mandatory.', 'blank': 'Bank account number is mandatory.'}
    )
    bank_ifsc = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=11,
        error_messages={'required': 'Bank IFSC code is mandatory.', 'blank': 'Bank IFSC code is mandatory.'}
    )
    account_holder_name = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=255,
        error_messages={'required': 'Account holder name is mandatory.', 'blank': 'Account holder name is mandatory.'}
    )

    class Meta:
        model = Employee
        exclude = ['tenant']
        read_only_fields = ['id', 'employee_code', 'created_at', 'updated_at']

    def validate_date_of_birth(self, value):
        if value:
            from django.utils import timezone
            today = timezone.now().date()
            age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
            if age < 13:
                raise serializers.ValidationError("Employee must be at least 13 years old.")
        return value

    def get_current_ctc(self, obj):
        latest = obj.salary_assignments.order_by('-effective_from').first()
        if latest:
            return str(latest.monthly_ctc)
        latest_hist = obj.salary_history.order_by('-effective_date', '-created_at').first()
        if latest_hist:
            return str(latest_hist.new_salary)
        return '0.00'

    def get_salary_details(self, obj):
        latest = obj.salary_assignments.order_by('-effective_from', '-id').first()
        if not latest or latest.monthly_ctc <= 0:
            return None
        ctc = latest.monthly_ctc
        components = dict(latest.computed_components or {})
        
        basic = Decimal(str(components.get('Basic') or components.get('basic') or (ctc * Decimal('0.50')))).quantize(Decimal('0.01'))
        hra = Decimal(str(components.get('HRA') or components.get('hra') or (basic * Decimal('0.50')))).quantize(Decimal('0.01'))
        da = Decimal(str(components.get('DA') or components.get('da') or '0.00')).quantize(Decimal('0.01'))
        special = Decimal(str(components.get('Special Allowance') or components.get('special_allowance') or (ctc - basic - hra - da))).quantize(Decimal('0.01'))

        gross = ctc
        # PF 12%
        epf = (basic * Decimal('0.12')).quantize(Decimal('0.01'))
        # ESI 0.75% if gross <= 21k
        esi = (gross * Decimal('0.0075')).quantize(Decimal('0.01')) if gross <= Decimal('21000.00') else Decimal('0.00')
        
        from hr.services.payroll_engine import compute_pt
        pt = compute_pt(gross, obj.work_state)

        from hr.services.tds_service import compute_tax_on_income
        std_ded = Decimal('75000.00') if obj.tax_regime == 'new' else Decimal('50000.00')
        annual_gross = (gross * Decimal('12')).quantize(Decimal('0.01'))
        taxable = max(Decimal('0.00'), annual_gross - std_ded)
        _, _, _, _, annual_tax = compute_tax_on_income(taxable, regime=obj.tax_regime)
        estimated_tds = (annual_tax / Decimal('12')).quantize(Decimal('0.01'))

        total_deductions = epf + esi + pt + estimated_tds
        net_pay = max(Decimal('0.00'), gross - total_deductions)
        annual_net_pay = (net_pay * Decimal('12')).quantize(Decimal('0.01'))

        # Employer contributions
        employer_epf = (basic * Decimal('0.0367')).quantize(Decimal('0.01'))
        employer_eps = min(basic * Decimal('0.0833'), Decimal('1250.00')).quantize(Decimal('0.01'))
        employer_esi = (gross * Decimal('0.0325')).quantize(Decimal('0.01')) if gross <= Decimal('21000.00') else Decimal('0.00')
        total_employer = employer_epf + employer_eps + employer_esi

        is_rebate = taxable <= (Decimal('1200000.00') if obj.tax_regime == 'new' else Decimal('500000.00'))

        comp_dict = {
            'Basic': str(basic),
            'HRA': str(hra),
            'DA': str(da),
            'Special Allowance': str(special),
            'basic': str(basic),
            'hra': str(hra),
            'da': str(da),
            'special_allowance': str(special),
            **{k: str(v) for k, v in components.items() if k not in ['Basic', 'HRA', 'DA', 'Special Allowance', 'basic', 'hra', 'da', 'special_allowance']}
        }

        deductions_dict = {
            'employee_pf': str(epf),
            'employee_esi': str(esi),
            'professional_tax': str(pt),
            'monthly_tds': str(estimated_tds),
            'annual_tds': str(annual_tax),
            'total_deductions': str(total_deductions),
        }

        employer_dict = {
            'employer_epf': str(employer_epf),
            'employer_eps': str(employer_eps),
            'employer_esi': str(employer_esi),
            'total_employer_cost': str(total_employer),
            'total_employer_contribution': str(total_employer),
        }

        tds_dict = {
            'monthly_tds': str(estimated_tds),
            'annual_tax': str(annual_tax),
            'annual_net_tax': str(annual_tax),
            'taxable_income': str(taxable),
            'standard_deduction': str(std_ded),
            'rebate_applied': is_rebate,
            'reason': 'Zero tax under rebate limit' if is_rebate and annual_tax == Decimal('0.00') else '',
        }

        return {
            'monthly_ctc': str(ctc),
            'annual_ctc': str((ctc * Decimal('12')).quantize(Decimal('0.01'))),
            'gross_salary': str(gross),
            'monthly_gross': str(gross),
            'annual_gross': str(annual_gross),
            'estimated_net_salary': str(net_pay),
            'monthly_net_take_home': str(net_pay),
            'net_take_home_monthly': str(net_pay),
            'annual_net_take_home': str(annual_net_pay),
            'net_take_home_annual': str(annual_net_pay),
            'salary_structure_id': str(latest.salary_structure_id),
            'salary_structure_name': latest.salary_structure.name,
            'effective_from': str(latest.effective_from),
            'components': comp_dict,
            'earnings': comp_dict,
            'employee_deductions': deductions_dict,
            'statutory_estimates': deductions_dict,
            'employer_contributions': employer_dict,
            'employer_estimates': employer_dict,
            'tds_details': tds_dict,
            'tax_regime': obj.tax_regime,
        }

    def validate_user(self, value):
        if value:
            request = self.context.get('request')
            if request and hasattr(request, 'user'):
                tenant = getattr(request.user, 'active_tenant', request.user)
                user_tenant = getattr(value, 'active_tenant', value)
                if user_tenant != tenant and value != tenant:
                    raise serializers.ValidationError("Assigned user does not belong to the active tenant.")
        return value

    def create(self, validated_data):
        salary_data = self.initial_data.get('salary') or self.initial_data.get('salary_assignment')
        declaration_data = self.initial_data.get('tax_declaration')
        employee = super().create(validated_data)
        self._handle_salary_assignment(employee, salary_data)
        self._handle_tax_declaration(employee, declaration_data)
        return employee

    def update(self, instance, validated_data):
        salary_data = self.initial_data.get('salary') or self.initial_data.get('salary_assignment')
        declaration_data = self.initial_data.get('tax_declaration')
        employee = super().update(instance, validated_data)
        if salary_data is not None:
            self._handle_salary_assignment(employee, salary_data)
        if declaration_data is not None:
            self._handle_tax_declaration(employee, declaration_data)
        return employee

    def _handle_salary_assignment(self, employee, salary_data):
        if not salary_data or not isinstance(salary_data, dict):
            return
        monthly_ctc = salary_data.get('monthly_ctc')
        if not monthly_ctc or float(monthly_ctc) <= 0:
            return

        ctc = Decimal(str(monthly_ctc)).quantize(Decimal('0.01'))
        effective_from = salary_data.get('effective_from') or employee.date_of_joining or timezone.now().date()
        tenant = employee.tenant

        structure_id = salary_data.get('salary_structure_id') or salary_data.get('salary_structure')
        structure = None
        if structure_id:
            structure = SalaryStructure.objects.filter(id=structure_id, tenant=tenant).first()
        if not structure:
            structure = SalaryStructure.objects.filter(tenant=tenant).first()
            if not structure:
                structure = SalaryStructure.objects.create(
                    tenant=tenant,
                    name="Standard Salary Structure",
                    description="Standard Indian compensation structure"
                )
                SalaryComponent.objects.create(
                    salary_structure=structure, name="Basic", type="earning",
                    component_type="pct_gross", value=Decimal('50.00'), is_basic=True, is_taxable=True, order=1
                )
                SalaryComponent.objects.create(
                    salary_structure=structure, name="HRA", type="earning",
                    component_type="pct_basic", value=Decimal('50.00'), is_basic=False, is_taxable=True, order=2
                )
                SalaryComponent.objects.create(
                    salary_structure=structure, name="Special Allowance", type="earning",
                    component_type="fixed", value=Decimal('0.00'), is_basic=False, is_taxable=True, order=3
                )

        custom_components = salary_data.get('components') or {}
        latest_existing = employee.salary_assignments.order_by('-effective_from', '-id').first()
        prev_salary = latest_existing.monthly_ctc if latest_existing else Decimal('0.00')

        if latest_existing:
            # If explicit effective_from is given and strictly after latest_existing, create/update a new future assignment
            if salary_data.get('effective_from') and str(salary_data.get('effective_from')) > str(latest_existing.effective_from):
                assignment, _ = EmployeeSalaryAssignment.objects.get_or_create(
                    tenant=tenant,
                    employee=employee,
                    effective_from=salary_data['effective_from'],
                    defaults={'salary_structure': structure, 'monthly_ctc': ctc}
                )
                assignment.salary_structure = structure
                assignment.monthly_ctc = ctc
                assignment.save(update_fields=['salary_structure', 'monthly_ctc'])
            else:
                # Update the active/latest assignment so current_ctc and salary_details immediately reflect the edit
                assignment = latest_existing
                assignment.salary_structure = structure
                assignment.monthly_ctc = ctc
                assignment.save(update_fields=['salary_structure', 'monthly_ctc'])
        else:
            assignment = EmployeeSalaryAssignment.objects.create(
                tenant=tenant,
                employee=employee,
                salary_structure=structure,
                effective_from=effective_from,
                monthly_ctc=ctc,
            )

        if custom_components:
            computed = {}
            for k, v in custom_components.items():
                try:
                    computed[k] = str(Decimal(str(v)).quantize(Decimal('0.01')))
                except Exception:
                    computed[k] = str(v)
            assignment.computed_components = computed
            assignment.save(update_fields=['computed_components'])
        else:
            assignment_serializer = EmployeeSalaryAssignmentSerializer(context=self.context)
            assignment_serializer._compute_components(assignment)

        from hr.models import EmployeeSalaryHistory
        if prev_salary != ctc or not employee.salary_history.exists():
            EmployeeSalaryHistory.objects.create(
                tenant=tenant,
                employee=employee,
                effective_date=assignment.effective_from,
                previous_salary=prev_salary,
                new_salary=ctc,
                salary_structure=structure,
                reason=salary_data.get('reason') or ("Initial salary assignment" if prev_salary == Decimal('0.00') else "Salary revision")
            )

    def _handle_tax_declaration(self, employee, declaration_data):
        if not declaration_data or not isinstance(declaration_data, dict):
            return
        from hr.services.tds_service import get_financial_year_info
        today = timezone.now().date()
        fy_info = get_financial_year_info(today.month, today.year)
        fy_str = declaration_data.get('financial_year') or fy_info['fy_string']
        regime = declaration_data.get('regime') or declaration_data.get('tax_regime') or employee.tax_regime or 'new'

        if regime != employee.tax_regime:
            employee.tax_regime = regime
            employee.save(update_fields=['tax_regime'])

        defaults = {
            'regime': regime,
            'section_80c': Decimal(str(declaration_data.get('section_80c') or 0)),
            'section_80d': Decimal(str(declaration_data.get('section_80d') or 0)),
            'section_24b_home_loan': Decimal(str(declaration_data.get('section_24b_home_loan') or 0)),
            'hra_exemption': Decimal(str(declaration_data.get('hra_exemption') or 0)),
            'other_exemptions': Decimal(str(declaration_data.get('other_exemptions') or 0)),
            'declared_previous_income': Decimal(str(declaration_data.get('declared_previous_income') or 0)),
            'declared_previous_tds': Decimal(str(declaration_data.get('declared_previous_tds') or 0)),
            'proof_submitted': bool(declaration_data.get('proof_submitted', False)),
            'proof_verified': bool(declaration_data.get('proof_verified', False)),
            'notes': declaration_data.get('notes', ''),
        }

        EmployeeTaxDeclaration.objects.update_or_create(
            tenant=employee.tenant,
            employee=employee,
            financial_year=fy_str,
            defaults=defaults,
        )


class EmployeeTaxDeclarationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)

    class Meta:
        model = EmployeeTaxDeclaration
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']


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

    def validate(self, attrs):
        employee = attrs.get('employee') or getattr(self.instance, 'employee', None)
        date = attrs.get('date') or getattr(self.instance, 'date', None)
        if employee and date and employee.date_of_joining:
            if date < employee.date_of_joining:
                raise serializers.ValidationError({
                    'date': f"Attendance date ({date}) cannot be prior to employee joining date ({employee.date_of_joining})."
                })
        return attrs


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
        self._log_salary_history(assignment, previous_salary=Decimal('0.00'), reason="Initial salary structure assignment")
        return assignment

    def update(self, instance, validated_data):
        old_ctc = instance.monthly_ctc
        assignment = super().update(instance, validated_data)
        self._compute_components(assignment)
        if old_ctc != assignment.monthly_ctc:
            self._log_salary_history(assignment, previous_salary=old_ctc, reason="Salary revision / increment")
        return assignment

    def _log_salary_history(self, assignment, previous_salary, reason):
        from hr.models import EmployeeSalaryHistory
        EmployeeSalaryHistory.objects.create(
            tenant=assignment.tenant,
            employee=assignment.employee,
            effective_date=assignment.effective_from,
            previous_salary=previous_salary,
            new_salary=assignment.monthly_ctc,
            salary_structure=assignment.salary_structure,
            reason=reason,
        )

    def _compute_components(self, assignment):
        ctc = Decimal(str(assignment.monthly_ctc))
        components = assignment.salary_structure.components.filter(is_active=True)

        basic_comp = next((c for c in components if c.is_basic), None)
        if not basic_comp:
            raise serializers.ValidationError("Salary structure must have a basic component.")

        computed = {}
        if basic_comp.component_type == 'fixed':
            basic_value = Decimal(str(basic_comp.value))
        else:
            basic_value = ctc * (Decimal(str(basic_comp.value)) / Decimal('100.0'))

        total_earnings = Decimal('0.0')
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
            if comp.type == 'earning':
                total_earnings += val

        # Balancing component: If total earnings < ctc, allocate remainder to Special Allowance
        if total_earnings < ctc:
            existing_sa = Decimal(str(computed.get('Special Allowance', '0.00')))
            if 'Special Allowance' not in computed or existing_sa == Decimal('0.00'):
                computed['Special Allowance'] = str(round(ctc - total_earnings, 2))

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
    reopened_by_name = serializers.CharField(source='reopened_by.username', read_only=True, default='')
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
            'is_reopened', 'reopened_at', 'reopened_by', 'reopen_history',
            'created_at', 'updated_at'
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
    month = serializers.IntegerField(source='payroll_run.month', read_only=True)
    year = serializers.IntegerField(source='payroll_run.year', read_only=True)
    payroll_run_status = serializers.CharField(source='payroll_run.status', read_only=True)

    earnings_breakdown = serializers.SerializerMethodField()
    deductions_breakdown = serializers.SerializerMethodField()
    attendance_summary = serializers.SerializerMethodField()
    employer_contributions = serializers.SerializerMethodField()
    deduction_reasons = serializers.SerializerMethodField()
    lop_amount = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at']

    def get_earnings_breakdown(self, obj):
        eb = dict(obj.earnings or {})
        if not eb and obj.gross_salary > 0:
            eb['Base Earnings'] = str(obj.gross_salary)
        if obj.overtime_amount > 0 and 'Overtime' not in eb:
            eb['Overtime'] = str(obj.overtime_amount)
        return eb

    def get_deductions_breakdown(self, obj):
        db = dict(obj.deductions or {})
        # Ensure standard statutory items are present if calculated on payslip
        if obj.employee_pf > 0 and 'PF' not in db and 'Provident Fund (PF)' not in db:
            db['Provident Fund (PF)'] = str(obj.employee_pf)
        if obj.employee_esi > 0 and 'ESI' not in db:
            db['ESI'] = str(obj.employee_esi)
        if obj.tds > 0 and 'TDS' not in db and 'Income Tax (TDS)' not in db:
            db['Income Tax (TDS)'] = str(obj.tds)
        if obj.professional_tax > 0 and 'PT' not in db and 'Professional Tax (PT)' not in db:
            db['Professional Tax (PT)'] = str(obj.professional_tax)
        if obj.advance_recovery > 0 and 'Salary Advance Recovery' not in db:
            db['Salary Advance Recovery'] = str(obj.advance_recovery)
        if obj.loan_recovery > 0 and 'Loan Recovery' not in db:
            db['Loan Recovery'] = str(obj.loan_recovery)

        # Loss of Pay docked amount if any
        if obj.lop_days > 0 and 'Loss of Pay (LOP)' not in db:
            working_days = Decimal(str(obj.total_working_days or 26))
            present_days = Decimal(str(obj.present_days or 0))
            if present_days > 0:
                daily_rate = Decimal(str(obj.gross_salary)) / present_days
            else:
                daily_rate = Decimal(str(obj.gross_salary)) / working_days
            lop_amt = (Decimal(str(obj.lop_days)) * daily_rate).quantize(Decimal('0.01'))
            if lop_amt > 0:
                db['Loss of Pay (LOP)'] = str(lop_amt)

        # Fallback if total_deductions > 0 but db is still empty
        if not db and obj.total_deductions > 0:
            db['Statutory / Payroll Deductions'] = str(obj.total_deductions)
        return db

    def get_deduction_reasons(self, obj):
        reasons = {}
        raw_reasons = obj.deduction_reasons or {}
        # Normalize any nested dictionary structures into clear strings
        for k, v in raw_reasons.items():
            if isinstance(v, dict):
                reasons[k] = v.get('reason') or v.get('amount') or str(v)
            else:
                reasons[k] = str(v)

        # Provide human-friendly explanations for standard deductions if missing
        if obj.employee_pf > 0:
            reasons.setdefault('PF', "Employee statutory 12.00% Provident Fund contribution on Basic salary")
            reasons.setdefault('Provident Fund (PF)', "Employee statutory 12.00% Provident Fund contribution on Basic salary")
        if obj.employee_esi > 0:
            reasons.setdefault('ESI', "Employee statutory 0.75% State Insurance contribution on gross earnings")
        if obj.tds > 0:
            reasons.setdefault('TDS', "Monthly income tax withholding deducted under Section 192")
            reasons.setdefault('Income Tax (TDS)', "Monthly income tax withholding deducted under Section 192")
        if obj.professional_tax > 0:
            reasons.setdefault('PT', "State statutory Professional Tax slab deduction")
            reasons.setdefault('Professional Tax (PT)', "State statutory Professional Tax slab deduction")
        if obj.advance_recovery > 0:
            reasons.setdefault('Salary Advance Recovery', "Monthly installment recovered against active salary advance")
        if obj.loan_recovery > 0:
            reasons.setdefault('Loan Recovery', "Monthly personal loan recovery installment")
        if obj.lop_days > 0:
            reasons.setdefault(
                'Loss of Pay (LOP)',
                f"{obj.lop_days} day(s) unpaid leave / absence docked from monthly salary based on {obj.total_working_days} working days"
            )
        return reasons

    def get_attendance_summary(self, obj):
        return {
            'working_days': obj.total_working_days,
            'present_days': float(obj.present_days),
            'paid_leaves': float(obj.paid_leave_days),
            'unpaid_leaves': float(obj.lop_days),
            'absent_days': float(obj.absent_days),
            'overtime_hours': float(obj.overtime_hours),
            'overtime_amount': float(obj.overtime_amount),
        }

    def get_employer_contributions(self, obj):
        return {
            'Employer EPF': str(obj.employer_epf or obj.employer_pf),
            'Employer EPS': str(obj.employer_eps),
            'Employer ESI': str(obj.employer_esi),
            'Total Employer Benefits': str(obj.employer_total_contribution),
        }

    def get_lop_amount(self, obj):
        if obj.lop_days > 0:
            working_days = Decimal(str(obj.total_working_days or 26))
            present_days = Decimal(str(obj.present_days or 0))
            if present_days > 0:
                daily_rate = Decimal(str(obj.gross_salary)) / present_days
            else:
                daily_rate = Decimal(str(obj.gross_salary)) / working_days
            return str((Decimal(str(obj.lop_days)) * daily_rate).quantize(Decimal('0.01')))
        return '0.00'


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

    def validate_salary_rounding(self, value):
        mapping = {
            'nearest_1': 'nearest_one',
            'nearest_10': 'nearest_ten',
            'exact_2': 'exact',
        }
        return mapping.get(value, value)

    def validate_lop_calculation_rule(self, value):
        mapping = {
            'exclude_sat_sun': 'working_days_5',
            '5_day_week': 'working_days_5',
        }
        return mapping.get(value, value)


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


class EmployeeAllowanceBonusSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_code = serializers.CharField(source='employee.employee_code', read_only=True)
    record_type_display = serializers.CharField(source='get_record_type_display', read_only=True)

    class Meta:
        model = EmployeeAllowanceBonus
        exclude = ['tenant']
        read_only_fields = ['id', 'created_at', 'updated_at']

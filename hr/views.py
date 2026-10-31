# HR DRF ViewSets and function-based views — implemented in tasks 6–16

import logging
from decimal import Decimal
from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import (
    Department, Designation, Employee, AttendanceRecord,
    LeaveType, LeaveBalance, LeaveApplication,
    SalaryStructure, SalaryComponent, EmployeeSalaryAssignment,
    PayrollRun, Payslip, EmployeeSalaryHistory, OvertimeRecord,
    EmployeeAdvanceLoan, LoanRecoveryLog, PayrollException,
    HRDocument, HRMSSettings, EmployeeAllowanceBonus,
    EmployeeTaxDeclaration
)
from .serializers import (
    DepartmentSerializer, DesignationSerializer, EmployeeSerializer,
    AttendanceRecordSerializer, LeaveTypeSerializer, LeaveBalanceSerializer,
    LeaveApplicationSerializer, SalaryStructureSerializer, EmployeeSalaryAssignmentSerializer,
    PayrollRunSerializer, PayslipSerializer, EmployeeSalaryHistorySerializer,
    OvertimeRecordSerializer, EmployeeAdvanceLoanSerializer, LoanRecoveryLogSerializer,
    PayrollExceptionSerializer, HRDocumentSerializer, HRMSSettingsSerializer,
    EmployeeAllowanceBonusSerializer, EmployeeTaxDeclarationSerializer
)
from .permissions import HRPermission
from .services import audit_service, leave_service
from .services.hr_accounting_service import HRAccountingService
from .services.exceptions_service import scan_payroll_exceptions
from .services.payroll_engine import run_payroll, get_hrms_settings


class DepartmentViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Department.

    - Queryset is scoped to the active tenant (Requirement 12.1, 12.3).
    - perform_create() sets tenant and writes a CREATE audit log (Requirement 13.1).
    - perform_update() captures before-state and writes an UPDATE audit log (Requirement 13.1).
    - perform_destroy() blocks deletion when active employees are assigned (Requirement 3.3)
      and writes a DELETE audit log (Requirement 13.1).

    Requirements: 3.1, 3.2, 3.3, 3.5, 12.1, 12.2, 12.3, 13.1
    """

    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return Department.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(
            self.request,
            instance,
            changes={'name': instance.name},
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'name': instance.name}
        updated = serializer.save()
        after = {'name': updated.name}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        # Block deletion if any active employees are assigned to this department
        assigned_count = Employee.objects.filter(department=instance, status='active').count()
        if assigned_count > 0:
            raise ValidationError(
                f"Cannot delete department '{instance.name}': "
                f"{assigned_count} active employee(s) are assigned to it."
            )
        # Delete non-active employees assigned to this department to avoid ProtectedError
        Employee.objects.filter(department=instance).exclude(status='active').delete()
        audit_service.log_delete(self.request, instance)
        instance.delete()


class DesignationViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Designation.

    - Queryset is scoped to the active tenant (Requirement 12.1, 12.3).
    - perform_create() sets tenant and writes a CREATE audit log (Requirement 13.1).
    - perform_update() captures before-state and writes an UPDATE audit log (Requirement 13.1).
    - perform_destroy() blocks deletion when active employees are assigned (Requirement 3.4)
      and writes a DELETE audit log (Requirement 13.1).

    Requirements: 3.1, 3.2, 3.4, 3.5, 12.1, 12.2, 12.3, 13.1
    """

    serializer_class = DesignationSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return Designation.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(
            self.request,
            instance,
            changes={'name': instance.name},
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'name': instance.name}
        updated = serializer.save()
        after = {'name': updated.name}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        # Block deletion if any active employees are assigned to this designation
        assigned_count = Employee.objects.filter(designation=instance, status='active').count()
        if assigned_count > 0:
            raise ValidationError(
                f"Cannot delete designation '{instance.name}': "
                f"{assigned_count} active employee(s) are assigned to it."
            )
        # Delete non-active employees assigned to this designation to avoid ProtectedError
        Employee.objects.filter(designation=instance).exclude(status='active').delete()
        audit_service.log_delete(self.request, instance)
        instance.delete()


class EmployeeViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Employee.
    
    - Queryset is scoped to the active tenant.
    - Supports status filtering via query params (?status=active).
    - perform_create() sets tenant, validates user FK, and writes a CREATE audit log.
    - perform_update() captures before-state and writes an UPDATE audit log.
    - perform_destroy() writes a DELETE audit log.

    Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 12.1, 12.2, 12.3, 13.1
    """

    serializer_class = EmployeeSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        qs = Employee.objects.filter(tenant=tenant)
        
        if self.request.user.role == 'employee':
            qs = qs.filter(user=self.request.user)
            
        status = self.request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
            
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        
        # User FK validation is also handled in the serializer
        instance = serializer.save(tenant=tenant)
        
        # Automatically create user if personal_email is provided
        if instance.personal_email and not instance.user:
            from django.contrib.auth import get_user_model
            from hr.tasks import send_employee_account_creation_email
            import random
            import string
            User = get_user_model()
            username = instance.personal_email
            
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            user = User.objects.create_user(
                username=username,
                email=instance.personal_email,
                password=password,
                role='employee',
                parent=tenant
            )
            instance.user = user
            instance.save(update_fields=['user'])
            
            from django.conf import settings
            use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
            if use_celery:
                send_employee_account_creation_email.delay(instance.personal_email, instance.full_name, password, tenant.business_name or "Cenvoras")
            else:
                send_employee_account_creation_email(instance.personal_email, instance.full_name, password, tenant.business_name or "Cenvoras")

        # Automatic branded offer letter dispatch if enabled in HRMSSettings
        settings_obj = getattr(tenant, 'hrms_settings', None)
        if settings_obj and getattr(settings_obj, 'auto_send_offer_letter', False) and instance.personal_email:
            from hr.tasks import send_employee_offer_letter_email
            from django.conf import settings
            use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
            if use_celery:
                send_employee_offer_letter_email.delay(str(instance.id))
            else:
                send_employee_offer_letter_email(str(instance.id))

        audit_service.log_create(
            self.request,
            instance,
            changes={
                'employee_code': instance.employee_code,
                'full_name': instance.full_name,
                'status': instance.status,
            },
        )

        # Seed leave balances for current year so Leave Balances UI is never empty
        try:
            leave_service.ensure_employee_leave_balances(instance)
        except Exception:
            logger.exception('Failed to init leave balances for employee %s', instance.id)

    @action(detail=True, methods=['post'])
    def send_offer_letter(self, request, pk=None):
        instance = self.get_object()
        if not instance.personal_email:
            return Response({'error': 'Employee does not have a personal email address configured.'}, status=status.HTTP_400_BAD_REQUEST)
        from hr.tasks import send_employee_offer_letter_email
        from django.conf import settings
        use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
        if use_celery:
            send_employee_offer_letter_email.delay(str(instance.id))
        else:
            send_employee_offer_letter_email(str(instance.id))
        return Response({'message': f'Official offer letter queued for dispatch to {instance.full_name} ({instance.personal_email}).'}, status=status.HTTP_200_OK)

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {
            'full_name': instance.full_name,
            'status': instance.status,
            'department_id': str(instance.department_id) if instance.department_id else None,
            'designation_id': str(instance.designation_id) if instance.designation_id else None,
        }
        updated = serializer.save()
        after = {
            'full_name': updated.full_name,
            'status': updated.status,
            'department_id': str(updated.department_id) if updated.department_id else None,
            'designation_id': str(updated.designation_id) if updated.designation_id else None,
        }
        audit_service.log_update(self.request, updated, before=before, after=after)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        tenant = getattr(request.user, 'active_tenant', request.user)
        user_to_delete = instance.user

        try:
            audit_service.log_delete(self.request, instance)
            instance.delete()
        except ProtectedError:
            return Response(
                {'detail': 'Cannot delete employee because of protected related records (e.g. historical payroll or leaves). Please mark the employee as inactive or resigned instead.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception("Failed to delete employee %s", instance.id)
            return Response(
                {'detail': f'Failed to delete employee: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Clean up linked user account if it was an employee user for this tenant
        if user_to_delete and getattr(user_to_delete, 'role', None) == 'employee' and getattr(user_to_delete, 'parent', None) == tenant:
            try:
                user_to_delete.delete()
            except Exception as e:
                logger.warning("Could not delete linked user %s for employee: %s", user_to_delete.id, e)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def increment_salary(self, request, pk=None):
        try:
            instance = self.get_object()
            tenant = getattr(request.user, 'active_tenant', request.user)

            increment_percentage = request.data.get('increment_percentage')
            new_salary_input = request.data.get('new_salary')
            effective_from = request.data.get('effective_from') or request.data.get('effective_date')
            reason = request.data.get('reason', '')
            structure_id = request.data.get('salary_structure')

            if not effective_from:
                effective_from = timezone.now().date()

            latest_assignment = EmployeeSalaryAssignment.objects.filter(employee=instance).order_by('-effective_from').first()
            prev_ctc = latest_assignment.monthly_ctc if latest_assignment else Decimal('0.00')
            target_structure = latest_assignment.salary_structure if latest_assignment else None

            if structure_id:
                try:
                    target_structure = SalaryStructure.objects.get(id=structure_id, tenant=tenant)
                except SalaryStructure.DoesNotExist:
                    raise ValidationError("Specified salary structure not found.")

            if not target_structure:
                target_structure = SalaryStructure.objects.filter(tenant=tenant).first()
                if not target_structure:
                    target_structure, _ = SalaryStructure.objects.get_or_create(
                        tenant=tenant,
                        name="Standard Salary Structure",
                        defaults={"description": "Default company salary structure"}
                    )

            if not target_structure.components.filter(is_basic=True).exists():
                SalaryComponent.objects.get_or_create(
                    salary_structure=target_structure,
                    name="Basic",
                    defaults={
                        "type": "earning",
                        "component_type": "pct_gross",
                        "value": Decimal('50.00'),
                        "is_basic": True,
                        "is_taxable": True,
                        "order": 1,
                    }
                )
                SalaryComponent.objects.get_or_create(
                    salary_structure=target_structure,
                    name="HRA",
                    defaults={
                        "type": "earning",
                        "component_type": "pct_gross",
                        "value": Decimal('25.00'),
                        "is_basic": False,
                        "is_taxable": True,
                        "order": 2,
                    }
                )
                SalaryComponent.objects.get_or_create(
                    salary_structure=target_structure,
                    name="Special Allowance",
                    defaults={
                        "type": "earning",
                        "component_type": "pct_gross",
                        "value": Decimal('25.00'),
                        "is_basic": False,
                        "is_taxable": True,
                        "order": 3,
                    }
                )

            if new_salary_input is not None and str(new_salary_input).strip() != '':
                try:
                    new_ctc = Decimal(str(new_salary_input)).quantize(Decimal('0.01'))
                except Exception:
                    raise ValidationError("Invalid new_salary format.")
            elif increment_percentage is not None and str(increment_percentage).strip() != '':
                try:
                    pct = Decimal(str(increment_percentage))
                    new_ctc = (prev_ctc * (Decimal('1.00') + (pct / Decimal('100.0')))).quantize(Decimal('0.01'))
                except Exception:
                    raise ValidationError("increment_percentage must be a valid number.")
            else:
                raise ValidationError("Either increment_percentage or new_salary is required.")

            data = {
                'employee': instance.id,
                'salary_structure': target_structure.id,
                'effective_from': effective_from,
                'monthly_ctc': new_ctc
            }

            existing_assignment = EmployeeSalaryAssignment.objects.filter(
                employee=instance,
                effective_from=effective_from
            ).first()

            if existing_assignment:
                serializer = EmployeeSalaryAssignmentSerializer(
                    existing_assignment,
                    data=data,
                    partial=True,
                    context={'request': request}
                )
            else:
                serializer = EmployeeSalaryAssignmentSerializer(
                    data=data,
                    context={'request': request}
                )
            serializer.is_valid(raise_exception=True)
            serializer.save(tenant=tenant)

            # Record immutable EmployeeSalaryHistory
            EmployeeSalaryHistory.objects.create(
                tenant=tenant,
                employee=instance,
                effective_date=effective_from,
                previous_salary=prev_ctc,
                new_salary=new_ctc,
                salary_structure=target_structure,
                reason=reason or f"Salary revision to ₹{new_ctc}",
                approved_by=request.user if request.user.is_authenticated else None,
            )

            try:
                audit_service.log_create(
                    request,
                    instance,
                    model_name="EmployeeSalaryRevision",
                    changes={
                        'employee_code': instance.employee_code,
                        'previous_salary': str(prev_ctc),
                        'new_salary': str(new_ctc),
                        'reason': reason,
                    }
                )
            except Exception as audit_err:
                logger.warning(f"Audit log failed in increment_salary: {audit_err}")

            if instance.personal_email:
                try:
                    from hr.tasks import send_salary_increment_email
                    from django.conf import settings
                    use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
                    if use_celery:
                        send_salary_increment_email.delay(instance.personal_email, instance.full_name, str(new_ctc))
                    else:
                        send_salary_increment_email(instance.personal_email, instance.full_name, str(new_ctc))
                except Exception as email_err:
                    logger.warning(f"Failed to send salary increment email to {instance.personal_email}: {email_err}")

            return Response({
                'status': 'Salary updated successfully',
                'previous_ctc': str(prev_ctc),
                'new_ctc': str(new_ctc),
            }, status=status.HTTP_200_OK)
        except ValidationError as ve:
            detail = ve.detail if hasattr(ve, 'detail') else str(ve)
            return Response({'error': detail}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unhandled error in increment_salary for employee {pk}: {e}")
            return Response({'error': f"Failed to update salary: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def salary_history(self, request, pk=None):
        instance = self.get_object()
        history = instance.salary_history.all().order_by('-effective_date', '-created_at')
        serializer = EmployeeSalaryHistorySerializer(history, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def calculate_salary_breakdown(self, request):
        """
        Real-time interactive salary & tax calculator.
        Given monthly_ctc (or annual_ctc), tax_regime, and optional component overrides,
        returns instant computed breakdown of Basic, HRA, DA, Special Allowance,
        Employee PF, ESI, PT, dynamic projected TDS, Net Take-Home, and Employer contributions.
        """
        from hr.services.tds_service import preview_monthly_tds
        from hr.services.payroll_engine import compute_pt

        data = request.data
        monthly_ctc = data.get('monthly_ctc')
        annual_ctc = data.get('annual_ctc')

        if monthly_ctc is not None and str(monthly_ctc).strip() != '':
            try:
                ctc = Decimal(str(monthly_ctc)).quantize(Decimal('0.01'))
            except Exception:
                raise ValidationError("Invalid monthly_ctc format.")
        elif annual_ctc is not None and str(annual_ctc).strip() != '':
            try:
                ctc = (Decimal(str(annual_ctc)) / Decimal('12.0')).quantize(Decimal('0.01'))
            except Exception:
                raise ValidationError("Invalid annual_ctc format.")
        else:
            ctc = Decimal('0.00')

        regime = (data.get('tax_regime') or 'new').lower()
        tenant = getattr(request.user, 'active_tenant', request.user)
        from hr.services.work_state import resolve_work_state
        work_state = resolve_work_state(tenant=tenant, work_state=data.get('work_state') or '')
        custom_components = data.get('components') or {}

        if ctc <= Decimal('0.00'):
            return Response({
                'monthly_ctc': '0.00',
                'annual_ctc': '0.00',
                'contracted_monthly_ctc': '0.00',
                'contracted_annual_ctc': '0.00',
                'gross_salary': '0.00',
                'monthly_gross': '0.00',
                'annual_gross': '0.00',
                'ad_hoc_monthly_total': '0.00',
                'net_take_home_monthly': '0.00',
                'monthly_net_take_home': '0.00',
                'net_take_home_annual': '0.00',
                'annual_net_take_home': '0.00',
                'earnings': {'basic': '0.00', 'hra': '0.00', 'da': '0.00', 'special_allowance': '0.00'},
                'fixed_allowances': {},
                'ad_hoc_earnings': {},
                'employee_deductions': {'employee_pf': '0.00', 'employee_esi': '0.00', 'professional_tax': '0.00', 'monthly_tds': '0.00', 'total_deductions': '0.00'},
                'employer_contributions': {'employer_epf': '0.00', 'employer_eps': '0.00', 'employer_esi': '0.00', 'total_employer_cost': '0.00'},
                'tds_details': {
                    'regime': regime,
                    'tax_regime': regime,
                    'standard_deduction': '0.00',
                    'taxable_income': '0.00',
                    'projected_annual_gross': '0.00',
                    'net_taxable_income': '0.00',
                    'annual_tax': '0.00',
                    'annual_net_tax': '0.00',
                    'monthly_tds': '0.00',
                    'rebate_applied': False,
                    'rebate_amount': '0.00',
                    'cess': '0.00',
                    'reason': 'No earnings provided.'
                }
            })

        basic_pct_val = data.get('basic_pct')
        hra_pct_val = data.get('hra_pct')
        basic_pct = Decimal(str(basic_pct_val if basic_pct_val is not None and str(basic_pct_val).strip() != '' else '50.0'))
        hra_pct_of_basic = Decimal(str(hra_pct_val if hra_pct_val is not None and str(hra_pct_val).strip() != '' else '50.0'))

        comp_basic = custom_components.get('basic') if 'basic' in custom_components else custom_components.get('Basic')
        if comp_basic is not None and str(comp_basic).strip() != '':
            basic = Decimal(str(comp_basic)).quantize(Decimal('0.01'))
        else:
            basic = (ctc * (basic_pct / Decimal('100.0'))).quantize(Decimal('0.01'))

        comp_hra = custom_components.get('hra') if 'hra' in custom_components else custom_components.get('HRA')
        if comp_hra is not None and str(comp_hra).strip() != '':
            hra = Decimal(str(comp_hra)).quantize(Decimal('0.01'))
        else:
            hra = (basic * (hra_pct_of_basic / Decimal('100.0'))).quantize(Decimal('0.01'))

        comp_da = custom_components.get('da') if 'da' in custom_components else custom_components.get('DA')
        da = Decimal(str(comp_da)).quantize(Decimal('0.01')) if comp_da is not None and str(comp_da).strip() != '' else Decimal('0.00')

        # Fixed recurring allowances (part of Contracted CTC)
        fixed_allowances_dict = {}
        subtotal_fixed_earnings = basic + hra + da
        for k, v in custom_components.items():
            if k.lower() not in ['basic', 'hra', 'da', 'special_allowance', 'special allowance']:
                try:
                    dec_val = Decimal(str(v)).quantize(Decimal('0.01'))
                    subtotal_fixed_earnings += dec_val
                    fixed_allowances_dict[k] = dec_val
                except Exception:
                    pass

        # Balancing Component: Special Allowance absorbs the remainder to equal 100% of Fixed Contracted CTC
        special_allowance = max(Decimal('0.00'), ctc - subtotal_fixed_earnings).quantize(Decimal('0.01'))
        contracted_gross_salary = subtotal_fixed_earnings + special_allowance

        # Ad-hoc / Variable Additions (outside Fixed Contracted CTC: Spot Bonus, Retention Bonus, Overtime)
        ad_hoc_components = data.get('ad_hoc_earnings') or {}
        ad_hoc_dict = {}
        ad_hoc_monthly_total = Decimal('0.00')
        for k, v in ad_hoc_components.items():
            try:
                val = Decimal(str(v)).quantize(Decimal('0.01'))
                if val > 0:
                    ad_hoc_monthly_total += val
                    ad_hoc_dict[k] = val
            except Exception:
                pass

        # Realized gross salary for this pay cycle (Contracted Gross + Ad-hoc Additions)
        gross_salary = contracted_gross_salary + ad_hoc_monthly_total

        hrms_settings = get_hrms_settings(tenant)
        from hr.services.payroll_engine import compute_pf, compute_esi
        pf_parts = compute_pf(basic, hrms_settings)
        esi_parts = compute_esi(gross_salary, hrms_settings)
        employee_pf = pf_parts['employee_pf']
        employee_esi = esi_parts['employee_esi']
        pt = compute_pt(gross_salary, work_state, tenant=tenant)

        decl = data.get('declarations') or {}
        preview_month = data.get('month')
        preview_year = data.get('year')
        joining_raw = data.get('date_of_joining') or data.get('joining_date')
        tds_preview = preview_monthly_tds(
            monthly_gross=gross_salary,
            regime=regime,
            month=preview_month,
            year=preview_year,
            previous_tds=Decimal(str(decl.get('declared_previous_tds') or 0)),
            previous_income=Decimal(str(decl.get('declared_previous_income') or 0)),
            section_80c=Decimal(str(decl.get('section_80c') or 0)),
            section_80d=Decimal(str(decl.get('section_80d') or 0)),
            section_24b=Decimal(str(decl.get('section_24b_home_loan') or 0)),
            hra_exemption=Decimal(str(decl.get('hra_exemption') or 0)),
            date_of_joining=joining_raw,
        )
        monthly_tds = tds_preview['monthly_tds']
        annual_gross = tds_preview['projected_annual_gross']
        net_taxable = tds_preview['net_taxable_income']
        total_annual_tax = tds_preview['total_annual_tax']
        rebate_applied = tds_preview['rebate_applied']
        rebate_amount = tds_preview['rebate_amount']
        cess = tds_preview['cess']
        std_ded = tds_preview['standard_deduction']
        rebate_threshold = tds_preview['rebate_threshold']

        total_deductions = employee_pf + employee_esi + pt + monthly_tds
        net_take_home = max(Decimal('0.00'), gross_salary - total_deductions)

        employer_epf = pf_parts['employer_epf']
        employer_eps = pf_parts['employer_eps']
        employer_esi = esi_parts['employer_esi']
        total_employer_cost = ctc + employer_epf + employer_eps + employer_esi

        regime_label = "New Regime (Sec 115BAC)" if regime == 'new' else "Old Tax Regime"
        if monthly_tds == Decimal('0.00'):
            if rebate_applied and net_taxable <= rebate_threshold:
                tds_reason = f"TDS skipped ({regime_label}): Taxable income Rs. {net_taxable:,.2f} is within Section 87A rebate threshold (Annual tax Rs. 0.00)."
            else:
                tds_reason = f"TDS skipped ({regime_label}): Annual income does not cross taxable slab."
        else:
            if rebate_applied and net_taxable > rebate_threshold:
                excess = net_taxable - rebate_threshold
                tds_reason = (
                    f"TDS under Sec 192 with Marginal Relief ({regime_label}): "
                    f"Taxable income Rs. {net_taxable:,.2f} exceeds ₹{rebate_threshold:,.0f} threshold by Rs. {excess:,.2f}. "
                    f"Marginal relief applied – tax capped at excess Rs. {excess:,.2f}. "
                    f"Annual Tax Rs. {total_annual_tax:,.2f} (incl. 4% cess), Rs. {monthly_tds:,.2f}/month."
                )
            else:
                tds_reason = f"TDS under Sec 192 ({regime_label}): Annual Tax Rs. {total_annual_tax:,.2f} (incl. 4% cess) spread as Rs. {monthly_tds:,.2f}/mo."

        earnings_dict = {
            'basic': str(basic),
            'hra': str(hra),
            'da': str(da),
        }
        for k, v in fixed_allowances_dict.items():
            earnings_dict[k] = str(v)
        earnings_dict['special_allowance'] = str(special_allowance)
        for k, v in ad_hoc_dict.items():
            earnings_dict[f"[Ad-hoc] {k}"] = str(v)

        return Response({
            'monthly_ctc': str(ctc),
            'annual_ctc': str((ctc * Decimal('12.0')).quantize(Decimal('0.01'))),
            'contracted_monthly_ctc': str(ctc),
            'contracted_annual_ctc': str((ctc * Decimal('12.0')).quantize(Decimal('0.01'))),
            'gross_salary': str(gross_salary),
            'monthly_gross': str(gross_salary),
            'annual_gross': str(annual_gross),
            'contracted_gross_monthly': str(contracted_gross_salary),
            'ad_hoc_monthly_total': str(ad_hoc_monthly_total),
            'net_take_home_monthly': str(net_take_home),
            'monthly_net_take_home': str(net_take_home),
            'net_take_home_annual': str((net_take_home * Decimal('12.0')).quantize(Decimal('0.01'))),
            'annual_net_take_home': str((net_take_home * Decimal('12.0')).quantize(Decimal('0.01'))),
            'earnings': earnings_dict,
            'fixed_allowances': {k: str(v) for k, v in fixed_allowances_dict.items()},
            'ad_hoc_earnings': {k: str(v) for k, v in ad_hoc_dict.items()},
            'employee_deductions': {
                'employee_pf': str(employee_pf),
                'employee_esi': str(employee_esi),
                'professional_tax': str(pt),
                'monthly_tds': str(monthly_tds),
                'total_deductions': str(total_deductions),
            },
            'employer_contributions': {
                'employer_epf': str(employer_epf),
                'employer_eps': str(employer_eps),
                'employer_esi': str(employer_esi),
                'total_employer_cost': str(total_employer_cost),
                'actual_realized_monthly_ctc': str(total_employer_cost + ad_hoc_monthly_total),
            },
            'tds_details': {
                'regime': regime,
                'tax_regime': regime,
                'standard_deduction': str(std_ded),
                'taxable_income': str(net_taxable),
                'projected_annual_gross': str(annual_gross),
                'net_taxable_income': str(net_taxable),
                'annual_tax': str(total_annual_tax),
                'annual_net_tax': str(total_annual_tax),
                'monthly_tds': str(monthly_tds),
                'rebate_applied': rebate_applied,
                'rebate_amount': str(rebate_amount),
                'cess': str(cess),
                'reason': tds_reason,
            }
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get', 'post'])
    def documents(self, request, pk=None):
        instance = self.get_object()
        tenant = getattr(request.user, 'active_tenant', request.user)
        if request.method == 'GET':
            docs = instance.documents.all().order_by('-created_at')
            serializer = HRDocumentSerializer(docs, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        else:
            serializer = HRDocumentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save(tenant=tenant, employee=instance, uploaded_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)


class AttendanceViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for AttendanceRecord with upsert logic.
    Requirements: 4.1, 4.2, 4.3, 4.7, 12.1, 13.1
    """
    serializer_class = AttendanceRecordSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            qs = AttendanceRecord.objects.filter(employee__user=user).select_related('employee')
        else:
            tenant = getattr(user, 'active_tenant', user)
            qs = AttendanceRecord.objects.filter(tenant=tenant).select_related('employee')

        employee_id = self.request.query_params.get('employee') or self.request.query_params.get('employee_id')
        if employee_id:
            qs = qs.filter(employee_id=employee_id)

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(employee__full_name__icontains=search) |
                Q(employee__employee_code__icontains=search)
            )

        status_param = self.request.query_params.get('status')
        if status_param and status_param != 'all':
            qs = qs.filter(status=status_param)

        year = self.request.query_params.get('year')
        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except (ValueError, TypeError):
                pass

        month = self.request.query_params.get('month')
        if month:
            try:
                qs = qs.filter(date__month=int(month))
            except (ValueError, TypeError):
                pass

        start_date = self.request.query_params.get('start_date')
        if start_date:
            qs = qs.filter(date__gte=start_date)

        end_date = self.request.query_params.get('end_date')
        if end_date:
            qs = qs.filter(date__lte=end_date)

        return qs.order_by('-date')

    @action(detail=False, methods=['get'])
    def export(self, request):
        import csv
        from django.http import HttpResponse

        qs = self.filter_queryset(self.get_queryset())
        
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="attendance_export.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Employee Code',
            'Employee Name',
            'Department',
            'Designation',
            'Date',
            'Day',
            'Status',
            'Check In',
            'Check Out',
            'Notes'
        ])
        
        for rec in qs[:3000]:
            emp = rec.employee
            writer.writerow([
                emp.employee_code if emp else '',
                emp.full_name if emp else '',
                emp.department.name if emp and emp.department else '',
                emp.designation.name if emp and emp.designation else '',
                rec.date.isoformat(),
                rec.date.strftime('%A'),
                rec.get_status_display() if hasattr(rec, 'get_status_display') else rec.status,
                rec.check_in.strftime('%H:%M') if rec.check_in else '',
                rec.check_out.strftime('%H:%M') if rec.check_out else '',
                rec.notes or ''
            ])
            
        return response

    @action(detail=False, methods=['get'])
    def employee_summary(self, request):
        employee_id = request.query_params.get('employee_id') or request.query_params.get('employee')
        if not employee_id:
            return Response({'error': 'employee_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(request.user, 'active_tenant', request.user)
        try:
            emp = Employee.objects.select_related('department', 'designation').get(id=employee_id, tenant=tenant)
        except Employee.DoesNotExist:
            return Response({'error': 'Employee not found.'}, status=status.HTTP_404_NOT_FOUND)

        import calendar, datetime
        today = timezone.now().date()
        year = int(request.query_params.get('year', today.year))
        month = int(request.query_params.get('month', today.month))

        _, num_days = calendar.monthrange(year, month)
        start_date = datetime.date(year, month, 1)
        end_date = datetime.date(year, month, num_days)

        records = AttendanceRecord.objects.filter(
            employee=emp,
            date__range=(start_date, end_date)
        ).order_by('date')

        rec_map = {r.date: r for r in records}

        days_list = []
        present_count = 0
        half_day_count = 0
        leave_count = 0
        absent_count = 0
        holiday_count = 0

        # Paid leave days from approved leave apps (same engine as payroll)
        try:
            from hr.services.payroll_engine import (
                get_attendance_breakdown,
                get_hrms_settings as _get_hrms_settings,
                get_total_working_days,
            )
            settings_obj = _get_hrms_settings(tenant)
            working_days = int(get_total_working_days(
                month, year, settings_obj.lop_calculation_rule,
                weekend_rule=getattr(settings_obj, 'weekend_rule', 'sunday_only'),
                default_working_days=getattr(settings_obj, 'default_working_days', None),
            ))
            breakdown = get_attendance_breakdown(emp, month, year, working_days, settings_obj)
            paid_leave_days = float(breakdown.get('paid_leave_days') or 0)
            unpaid_leave_days = float(
                breakdown.get('unpaid_leave_days')
                or len(breakdown.get('unpaid_leave_dates') or [])
            )
        except Exception:
            paid_leave_days = 0.0
            unpaid_leave_days = 0.0

        for d in range(1, num_days + 1):
            cur_date = datetime.date(year, month, d)
            is_weekend = cur_date.isoweekday() in [7]
            rec = rec_map.get(cur_date)
            
            day_status = rec.status if rec else ('weekend' if is_weekend else 'unmarked')
            if rec:
                if rec.status == 'present':
                    present_count += 1
                elif rec.status == 'half_day':
                    half_day_count += 1
                elif rec.status == 'leave':
                    leave_count += 1
                elif rec.status == 'absent':
                    absent_count += 1
            elif is_weekend:
                holiday_count += 1

            check_in = getattr(rec, 'check_in', None) if rec else None
            check_out = getattr(rec, 'check_out', None) if rec else None
            days_list.append({
                'date': cur_date.isoformat(),
                'day': cur_date.strftime('%a'),
                'day_name': cur_date.strftime('%a'),
                'day_number': d,
                'status': day_status,
                'check_in': check_in.strftime('%H:%M') if check_in else None,
                'check_out': check_out.strftime('%H:%M') if check_out else None,
                'notes': (getattr(rec, 'notes', None) or '') if rec else '',
                'is_weekend': is_weekend,
            })

        effective_present = Decimal(str(present_count)) + (Decimal(str(half_day_count)) * Decimal('0.5'))

        return Response({
            'employee': {
                'id': str(emp.id),
                'name': emp.full_name,
                'code': emp.employee_code,
                'department': emp.department.name if emp.department else '',
                'designation': emp.designation.name if emp.designation else '',
                'employment_type': emp.get_employment_type_display(),
            },
            'month': month,
            'year': year,
            'month_name': calendar.month_name[month],
            'total_calendar_days': num_days,
            'present_days': present_count,
            'half_days': half_day_count,
            'leave_days': leave_count,
            'paid_leave_days': paid_leave_days,
            'unpaid_leave_days': unpaid_leave_days,
            'absent_days': absent_count,
            'weekends_holidays': holiday_count,
            'effective_present_days': str(effective_present),
            'attendance_rate': f"{(float(effective_present) / max(1, num_days - holiday_count) * 100):.1f}%",
            'days': days_list
        }, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request.user, 'active_tenant', request.user)
        employee = serializer.validated_data['employee']
        date = serializer.validated_data['date']
        att_status = serializer.validated_data['status']

        record = AttendanceRecord.objects.filter(employee=employee, date=date).first()
        if not record:
            # CREATE audit is written by the global audit_log signal — do not double-log.
            record = AttendanceRecord.objects.create(tenant=tenant, employee=employee, date=date, status=att_status)
            return Response(self.get_serializer(record).data, status=status.HTTP_201_CREATED)
        else:
            before = {'status': record.status}
            record.status = att_status
            record.save()
            # UPDATE audit via signal; keep explicit log only when signal has no diff
            if before.get('status') != att_status:
                pass  # post_save signal records the change
            return Response(self.get_serializer(record).data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()


class BulkAttendanceView(APIView):
    """
    Bulk upsert attendance records.
    Requirements: 4.4
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def post(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        data = request.data
        if not isinstance(data, list):
            return Response({"error": "Expected a list of attendance records."}, status=status.HTTP_400_BAD_REQUEST)

        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for item in data:
                employee_id = item.get('employee_id')
                date = item.get('date')
                att_status = item.get('status')
                
                try:
                    emp = Employee.objects.get(id=employee_id, tenant=tenant)
                except Employee.DoesNotExist:
                    continue

                if emp.date_of_joining:
                    import datetime
                    rec_date = datetime.date.fromisoformat(date) if isinstance(date, str) else date
                    if rec_date < emp.date_of_joining:
                        continue
                
                record = AttendanceRecord.objects.filter(employee=emp, date=date).first()
                if not record:
                    record = AttendanceRecord.objects.create(tenant=tenant, employee=emp, date=date, status=att_status)
                    audit_service.log_create(request, record, changes={'status': att_status})
                    created_count += 1
                else:
                    if record.status != att_status:
                        before = {'status': record.status}
                        record.status = att_status
                        record.save()
                        audit_service.log_update(request, record, before=before, after={'status': att_status})
                        updated_count += 1

        return Response({"created": created_count, "updated": updated_count}, status=status.HTTP_200_OK)


class LeaveTypeViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for LeaveType.
    Requirements: 5.1, 5.5, 13.1
    """
    serializer_class = LeaveTypeSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return LeaveType.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'name': instance.name})
        try:
            leave_service.ensure_leave_type_balances(instance)
        except Exception:
            logger.exception('Failed to init leave balances for leave type %s', instance.id)

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'name': instance.name, 'annual_entitlement': str(instance.annual_entitlement)}
        updated = serializer.save()
        after = {'name': updated.name, 'annual_entitlement': str(updated.annual_entitlement)}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        if LeaveApplication.objects.filter(leave_type=instance).exists():
            raise ValidationError(f"Cannot delete leave type '{instance.name}' as it is referenced by existing applications.")
        audit_service.log_delete(self.request, instance)
        instance.delete()


class LeaveBalanceViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for LeaveBalance.
    Requirements: 5.2, 5.5, 13.1
    """
    serializer_class = LeaveBalanceSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            return LeaveBalance.objects.filter(employee__user=user)
        tenant = getattr(user, 'active_tenant', user)
        return LeaveBalance.objects.filter(employee__tenant=tenant)

    def list(self, request, *args, **kwargs):
        # Lazily seed missing balances so UI shows zero rows with leave type names
        user = request.user
        if getattr(user, 'role', None) != 'employee':
            tenant = getattr(user, 'active_tenant', user)
            year = timezone.now().year
            for emp in Employee.objects.filter(tenant=tenant, status='active'):
                try:
                    leave_service.ensure_employee_leave_balances(emp, year=year)
                except Exception:
                    logger.exception('Failed to seed leave balances for employee %s', emp.id)
        return super().list(request, *args, **kwargs)

    def check_permissions(self, request):
        super().check_permissions(request)
        if getattr(request.user, 'role', None) == 'manager' and request.method not in permissions.SAFE_METHODS:
            self.permission_denied(request, message="Managers cannot modify leave balances.")

    def perform_create(self, serializer):
        instance = serializer.save()
        audit_service.log_create(self.request, instance, changes={'balance': str(instance.balance)})

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'balance': str(instance.balance)}
        updated = serializer.save()
        after = {'balance': str(updated.balance)}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()


class LeaveApplicationViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for LeaveApplication.
    Requirements: 6.1, 6.2, 6.3, 6.6, 13.1
    """
    serializer_class = LeaveApplicationSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            return LeaveApplication.objects.filter(employee__user=user).select_related('employee', 'leave_type')
        tenant = getattr(user, 'active_tenant', user)
        return LeaveApplication.objects.filter(tenant=tenant).select_related('employee', 'leave_type')

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        employee = serializer.validated_data['employee']
        
        if user.role == 'employee' and employee.user != user:
            raise ValidationError("You can only apply for leave for yourself.")
            
        start_date = serializer.validated_data['start_date']
        end_date = serializer.validated_data['end_date']
        leave_type = serializer.validated_data['leave_type']
        
        computed_days = leave_service.compute_leave_days(start_date, end_date, employee)

        if leave_type.is_paid:
            year = start_date.year
            bal_record = leave_service.get_or_init_leave_balance(employee, leave_type, year)
            if bal_record.balance <= 0:
                raise ValidationError({"leave_type": f"Your paid leave quota for '{leave_type.name}' is fully exhausted (0 days remaining). Additional paid leave cannot be taken."})
            if computed_days > bal_record.balance:
                raise ValidationError({"end_date": f"Insufficient paid leave balance. You have {bal_record.balance} days available for '{leave_type.name}', but requested {computed_days} days. Additional paid leave cannot be taken once your quota is exhausted."})
        
        instance = serializer.save(tenant=tenant, computed_days=computed_days, status='pending')
        audit_service.log_create(self.request, instance, changes={
            'status': 'pending', 
            'start_date': str(start_date),
            'end_date': str(end_date),
            'computed_days': str(computed_days)
        })

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'status': instance.status, 'start_date': str(instance.start_date), 'end_date': str(instance.end_date)}
        updated = serializer.save()
        after = {'status': updated.status, 'start_date': str(updated.start_date), 'end_date': str(updated.end_date)}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()


class LeaveApproveView(APIView):
    """
    Approve a leave application.
    Requirements: 5.3, 5.4, 6.4, 6.5, 6.6
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def post(self, request, pk, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        try:
            application = LeaveApplication.objects.get(pk=pk, tenant=tenant)
        except LeaveApplication.DoesNotExist:
            return Response({"error": "Leave application not found."}, status=status.HTTP_404_NOT_FOUND)

        if application.status != 'pending':
            return Response({"error": f"Cannot approve application with status {application.status}."}, status=status.HTTP_400_BAD_REQUEST)

        before = {'status': application.status, 'lwp_days': str(application.lwp_days)}

        with transaction.atomic():
            application.status = 'approved'
            year = application.start_date.year
            balance_record = leave_service.get_or_init_leave_balance(application.employee, application.leave_type, year)
            
            computed = application.computed_days
            available = balance_record.balance
            
            if computed > available:
                application.lwp_days = computed - available
                balance_record.balance = 0
            else:
                application.lwp_days = 0
                balance_record.balance = available - computed
                
            balance_record.save()
            application.save()

            import datetime
            current_date = application.start_date
            
            while current_date <= application.end_date:
                existing_holiday = AttendanceRecord.objects.filter(
                    employee=application.employee, date=current_date, status='holiday'
                ).exists()

                if current_date.isoweekday() != 7 and not existing_holiday:
                    att, created = AttendanceRecord.objects.update_or_create(
                        employee=application.employee,
                        date=current_date,
                        defaults={'status': 'leave', 'tenant': tenant}
                    )
                current_date += datetime.timedelta(days=1)

        after = {'status': application.status, 'lwp_days': str(application.lwp_days)}
        audit_service.log_update(request, application, before=before, after=after)
        return Response({"status": "approved", "lwp_days": application.lwp_days}, status=status.HTTP_200_OK)


class LeaveRejectView(APIView):
    """
    Reject a leave application.
    Requirements: 6.4, 6.5, 6.6
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def post(self, request, pk, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        try:
            application = LeaveApplication.objects.get(pk=pk, tenant=tenant)
        except LeaveApplication.DoesNotExist:
            return Response({"error": "Leave application not found."}, status=status.HTTP_404_NOT_FOUND)

        if application.status != 'pending':
            return Response({"error": f"Cannot reject application with status {application.status}."}, status=status.HTTP_400_BAD_REQUEST)

        before = {'status': application.status}
        application.status = 'rejected'
        application.save()
        after = {'status': application.status}
        
        audit_service.log_update(request, application, before=before, after=after)
        return Response({"status": "rejected"}, status=status.HTTP_200_OK)


class SalaryStructureViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for SalaryStructure.
    Requirements: 7.1, 7.6, 12.1, 13.1
    """
    serializer_class = SalaryStructureSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return SalaryStructure.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'name': instance.name})

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'name': instance.name}
        updated = serializer.save()
        after = {'name': updated.name}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        if EmployeeSalaryAssignment.objects.filter(salary_structure=instance).exists():
            raise ValidationError(f"Cannot delete salary structure '{instance.name}' as it is assigned to employees.")
        audit_service.log_delete(self.request, instance)
        instance.delete()


class SalaryAssignmentViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for EmployeeSalaryAssignment.
    Requirements: 7.4, 7.6, 12.1, 13.1
    """
    serializer_class = EmployeeSalaryAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return EmployeeSalaryAssignment.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'monthly_ctc': str(instance.monthly_ctc)})

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'monthly_ctc': str(instance.monthly_ctc)}
        updated = serializer.save()
        after = {'monthly_ctc': str(updated.monthly_ctc)}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()


from django.utils import timezone

class PayrollRunViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for PayrollRun.
    Requirements: 8.9, 8.10, 12.1, 13.1
    """
    serializer_class = PayrollRunSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]
    payroll_action = True

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return PayrollRun.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'month': instance.month, 'year': instance.year})

    def perform_update(self, serializer):
        instance = serializer.instance
        before = {'status': instance.status}
        updated = serializer.save()
        after = {'status': updated.status}
        audit_service.log_update(self.request, updated, before=before, after=after)

    def perform_destroy(self, instance):
        if instance.status == 'finalised':
            raise ValidationError("Cannot delete a finalised payroll run.")
        audit_service.log_delete(self.request, instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None):
        instance = self.get_object()
        tenant = getattr(request.user, 'active_tenant', request.user)

        if PayrollRun.objects.filter(tenant=tenant, month=instance.month, year=instance.year, status='finalised').exists():
            raise ValidationError("payroll_already_finalised")

        instance.status = 'processing'
        instance.save(update_fields=['status'])

        from hr.tasks import run_payroll_task
        from django.conf import settings
        from django.db import close_old_connections

        use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)

        if use_celery:
            run_payroll_task.delay(str(instance.id))
            audit_service.log_update(request, instance, before={'status': 'draft'}, after={'status': 'processing'})
            return Response({'status': 'Payroll run initiated'}, status=status.HTTP_202_ACCEPTED)
        else:
            run_payroll_task(str(instance.id))
            close_old_connections()
            instance.refresh_from_db()
            try:
                audit_service.log_update(request, instance, before={'status': 'processing'}, after={'status': instance.status})
            except Exception:
                pass
            return Response({'status': instance.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def calculate(self, request, pk=None):
        instance = self.get_object()
        if instance.status == 'locked':
            raise ValidationError("Cannot recalculate a locked payroll run. Please reopen first.")

        run_payroll(str(instance.id))
        instance.refresh_from_db()
        audit_service.log_update(request, instance, before={'status': 'draft'}, after={'status': instance.status})
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def exceptions(self, request, pk=None):
        instance = self.get_object()
        exceptions = instance.exceptions.all().order_by('severity', 'created_at')
        serializer = PayrollExceptionSerializer(exceptions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='bank_export')
    def bank_export(self, request, pk=None):
        """Generates a downloadable CSV batch payment file for corporate net banking."""
        import csv
        from django.http import HttpResponse
        instance = self.get_object()
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="salary_payout_{instance.month}_{instance.year}.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Beneficiary Account Number', 'Beneficiary Name', 'IFSC Code', 'Amount', 'Remarks', 'Employee Code'])
        
        for ps in instance.payslips.select_related('employee'):
            writer.writerow([
                ps.employee.bank_account_number or '',
                ps.employee.account_holder_name or ps.employee.full_name,
                ps.employee.bank_ifsc or '',
                f"{ps.net_salary:.2f}",
                f"Salary for {instance.month}/{instance.year}",
                ps.employee.employee_code,
            ])
        return response

    @action(detail=True, methods=['post'])
    def finalise(self, request, pk=None):
        """Backward-compatible action mapping to approval."""
        instance = self.get_object()
        if instance.status not in ['completed', 'calculated']:
            raise ValidationError("Only completed or calculated payroll runs can be finalised.")

        before = {'status': instance.status, 'finalised_at': str(instance.finalised_at)}
        instance.status = 'finalised'
        instance.finalised_at = timezone.now()
        instance.save(update_fields=['status', 'finalised_at'])

        after = {'status': instance.status, 'finalised_at': str(instance.finalised_at)}
        audit_service.log_update(request, instance, before=before, after=after)
        return Response({'status': 'Payroll run finalised'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        instance = self.get_object()
        if instance.status in ['approved', 'paid', 'locked']:
            raise ValidationError(f"Payroll run is already {instance.status}.")

        critical_exceptions = instance.exceptions.filter(severity='critical', is_resolved=False)
        if critical_exceptions.exists():
            count = critical_exceptions.count()
            details = []
            for exc in critical_exceptions:
                emp_label = f"{exc.employee.full_name} ({exc.employee.employee_code})" if exc.employee else "System"
                details.append(f"{emp_label}: {exc.message}")
            issues_text = "; ".join(details)
            err_msg = f"Cannot approve payroll ({count} unresolved critical issue{'s' if count > 1 else ''}): {issues_text}. Please review and resolve in the Exceptions panel before approving."
            raise ValidationError(err_msg)

        before = {'status': instance.status}
        instance.status = 'approved'
        instance.approved_by = request.user
        instance.approved_at = timezone.now()
        instance.save(update_fields=['status', 'approved_by', 'approved_at'])

        # Create Double-Entry Accounting Accrual entries in Ledger
        try:
            HRAccountingService.post_payroll_accrual(instance, request.user)
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Failed to post accounting accrual for payroll run %s", instance.id)
            instance.status = before.get('status', 'calculated')
            instance.approved_by = None
            instance.approved_at = None
            instance.save(update_fields=['status', 'approved_by', 'approved_at'])
            raise ValidationError(f"Accounting accrual failed: {str(exc)}. Please check ledger chart of accounts.")

        audit_service.log_update(request, instance, before=before, after={'status': 'approved'})
        return Response({'status': 'Payroll approved and accounting accrual entries posted successfully'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        instance = self.get_object()
        if instance.status not in ['approved', 'finalised']:
            raise ValidationError("Only approved payroll runs can be marked as paid.")

        payment_account_id = request.data.get('payment_account_id')
        if not payment_account_id:
            raise ValidationError(
                "payment_account_id is required. Select a Bank account for salary disbursement."
            )
        from ledger.models import Account, AccountType
        from ledger.services import AccountingService
        try:
            payment_account = Account.objects.get(id=payment_account_id, created_by=instance.tenant)
        except Account.DoesNotExist:
            raise ValidationError("Specified payment bank account not found.")

        from hr.services.payroll_engine import get_hrms_settings
        from hr.services.disbursement import classify_payment_account, cash_40a3_warnings
        kind = classify_payment_account(payment_account)
        if kind is None:
            raise ValidationError(
                "Select a Bank account for salary disbursement. "
                "Receivables and other asset accounts are not allowed."
            )
        if kind == 'cash' and not get_hrms_settings(instance.tenant).allow_cash_salary_disbursement:
            raise ValidationError(
                "Cash salary disbursement is disabled. Pay from a Bank account, "
                "or ask an admin to enable cash disbursement in HRMS settings."
            )
        code = str(getattr(payment_account, 'code', '') or '')

        # Required = sum of payslip nets (same figure credited to GL), not stale run.total_net
        from django.db.models import Sum as _Sum
        payslip_net = instance.payslips.aggregate(s=_Sum('net_salary'))['s']
        total_net = Decimal(str(payslip_net if payslip_net is not None else (instance.total_net or 0))).quantize(Decimal('0.01'))
        from ledger.balance_checks import assert_sufficient_balance
        # Hard block for Bank and (when enabled) Cash when the balance is insufficient.
        assert_sufficient_balance(payment_account, instance.tenant, total_net)
        warnings = cash_40a3_warnings(instance) if kind == 'cash' else []

        before = {'status': instance.status}
        instance.status = 'paid'
        instance.paid_by = request.user
        instance.paid_at = timezone.now()
        instance.payment_account = payment_account
        instance.save(update_fields=['status', 'paid_by', 'paid_at', 'payment_account'])

        # Create Double-Entry Accounting Disbursement entries and record loan recoveries
        HRAccountingService.post_payroll_disbursement(instance, payment_account, request.user)

        audit_service.log_update(request, instance, before=before, after={'status': 'paid'})
        return Response({
            'status': 'Payroll marked as paid and disbursement ledger entries recorded',
            'warnings': warnings,
            'payment_account_code': getattr(payment_account, 'code', None),
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        instance = self.get_object()
        if instance.status not in ['approved', 'paid', 'finalised']:
            raise ValidationError("Only approved or paid payroll runs can be locked.")

        before = {'status': instance.status}
        instance.status = 'locked'
        instance.locked_by = request.user
        instance.locked_at = timezone.now()
        instance.save(update_fields=['status', 'locked_by', 'locked_at'])

        audit_service.log_update(request, instance, before=before, after={'status': 'locked'})
        return Response({'status': 'Payroll run locked successfully'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        instance = self.get_object()
        if instance.status == 'draft':
            raise ValidationError("Payroll run is already in draft status.")

        if instance.status not in ['calculated', 'review', 'approved', 'paid', 'locked'] and not getattr(instance, 'finalised_at', None):
            raise ValidationError("Only processed, approved, paid, or locked payroll runs can be reopened.")

        role = getattr(request.user, 'role', '')
        is_owner_or_admin = (
            request.user.is_superuser
            or request.user.is_staff
            or getattr(request.user, 'parent', None) is None
            or role in ['admin', 'hr', 'accountant']
        )
        if not is_owner_or_admin:
            raise ValidationError("Only HR, Admin, or authorized roles are authorized to reopen payroll.")

        reason = request.data.get('reason', '').strip()
        if not reason:
            raise ValidationError("A mandatory reason must be provided to reopen payroll.")

        before = {'status': instance.status}
        # Reverse accounting entries and restore loans if previously approved/paid/locked
        HRAccountingService.reverse_payroll_accrual(instance, request.user, reason)

        now_dt = timezone.now()
        history = list(instance.reopen_history or [])
        user_display = f"{getattr(request.user, 'first_name', '')} {getattr(request.user, 'last_name', '')}".strip() or request.user.username
        history.append({
            'reopened_by': request.user.username,
            'reopened_by_name': user_display,
            'reopened_at': now_dt.isoformat(),
            'reason': reason,
        })

        instance.reopen_history = history
        instance.status = 'draft'
        instance.is_reopened = True
        instance.reopened_by = request.user
        instance.reopened_at = now_dt
        instance.reopen_reason = reason
        instance.approved_at = None
        instance.approved_by = None
        instance.paid_at = None
        instance.paid_by = None
        instance.locked_at = None
        instance.locked_by = None
        instance.save(update_fields=[
            'status', 'is_reopened', 'reopened_by', 'reopened_at', 'reopen_reason', 'reopen_history',
            'approved_at', 'approved_by', 'paid_at', 'paid_by', 'locked_at', 'locked_by'
        ])

        try:
            audit_service.log_update(
                request,
                instance,
                before=before,
                after={'status': 'draft', 'reopened_reason': reason},
                model_name="PayrollReopen"
            )
        except Exception:
            pass

        return Response({'status': 'Payroll run reopened and accounting entries reversed for editing'}, status=status.HTTP_200_OK)


class PayslipViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for Payslip with email and WhatsApp delivery actions.
    """
    serializer_class = PayslipSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]
    payroll_action = True

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            return Payslip.objects.filter(employee__user=user)
        tenant = getattr(user, 'active_tenant', user)
        qs = Payslip.objects.filter(tenant=tenant)
        run_id = self.request.query_params.get('payroll_run')
        if run_id:
            qs = qs.filter(payroll_run_id=run_id)
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs

    @action(detail=True, methods=['post'])
    def send_email(self, request, pk=None):
        payslip = self.get_object()
        if not payslip.employee.personal_email:
            raise ValidationError("Employee has no email address configured.")

        import calendar
        from integration.tasks import send_async_email_notification
        subject = f"Payslip for {calendar.month_name[payslip.payroll_run.month]} {payslip.payroll_run.year}"
        message = (
            f"Dear {payslip.employee.full_name},\n\n"
            f"Your payslip for {calendar.month_name[payslip.payroll_run.month]} {payslip.payroll_run.year} has been generated.\n\n"
            f"Gross Earnings: ₹{payslip.gross_salary}\n"
            f"Total Deductions: ₹{payslip.total_deductions}\n"
            f"Net Take-Home Salary: ₹{payslip.net_salary}\n\n"
            f"Thank you,\n{payslip.tenant.business_name or 'Cenvora HR'}"
        )
        send_async_email_notification.delay(
            str(payslip.tenant.id),
            payslip.employee.personal_email,
            subject,
            message,
            'Payslip',
            str(payslip.id)
        )
        return Response({'status': 'Payslip email queued successfully'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def send_whatsapp(self, request, pk=None):
        payslip = self.get_object()
        if not payslip.employee.personal_phone:
            raise ValidationError("Employee has no phone number configured.")

        import calendar
        from integration.tasks import send_async_whatsapp_notification
        body = (
            f"Hello {payslip.employee.full_name},\n"
            f"Your salary slip for {calendar.month_name[payslip.payroll_run.month]} {payslip.payroll_run.year} is ready.\n"
            f"Net Pay: ₹{payslip.net_salary}\n"
            f"— {payslip.tenant.business_name or 'Cenvora HR'}"
        )
        send_async_whatsapp_notification.delay(
            str(payslip.tenant.id),
            payslip.employee.personal_phone,
            body,
            'Payslip',
            str(payslip.id)
        )
        return Response({'status': 'Payslip WhatsApp message queued successfully'}, status=status.HTTP_200_OK)


from django.http import HttpResponse
from django.core.cache import cache
from hr.services.pdf_service import generate_payslip_pdf
from decimal import Decimal

class PayslipPDFView(APIView):
    """
    Download a payslip as a PDF.
    Requirements: 10.2, 10.3, 13.1
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]
    payroll_action = True

    def get(self, request, pk, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        try:
            payslip = Payslip.objects.get(pk=pk, tenant=tenant)
        except Payslip.DoesNotExist:
            return Response({"error": "Payslip not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.user.role == 'employee' and payslip.employee.user != request.user:
            return Response({"error": "You are not authorized to view this payslip."}, status=status.HTTP_403_FORBIDDEN)

        pdf_bytes = generate_payslip_pdf(payslip)
        
        audit_service.log_download(request, payslip)
        
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        filename = f"payslip_{payslip.employee.employee_code}_{payslip.payroll_run.month}_{payslip.payroll_run.year}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response


class HRDashboardView(APIView):
    """
    Dashboard metrics for HR module.
    Displays KPIs, current payroll progress, and HR alert notifications.
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        
        import pytz
        from django.db.models import Sum, Q
        tz = pytz.timezone('Asia/Kolkata')
        today = timezone.now().astimezone(tz).date()
        current_year = today.year
        current_month = today.month
        
        total_employees = Employee.objects.filter(tenant=tenant).count()
        active_employees = Employee.objects.filter(tenant=tenant, status='active').count()
        
        on_leave_today = AttendanceRecord.objects.filter(
            tenant=tenant,
            date=today,
            status='leave'
        ).count()
        
        present_today = AttendanceRecord.objects.filter(
            tenant=tenant,
            date=today,
            status__in=['present', 'half_day']
        ).count()
        
        # Current month payroll run
        current_payroll = PayrollRun.objects.filter(
            tenant=tenant,
            year=current_year,
            month=current_month
        ).first()

        payroll_this_month = Decimal('0.00')
        net_salary_payable = Decimal('0.00')
        pending_payroll_runs = PayrollRun.objects.filter(
            tenant=tenant,
            status__in=['draft', 'calculating', 'calculated', 'review', 'approved']
        ).count()

        if current_payroll:
            payroll_this_month = current_payroll.total_gross
            net_salary_payable = current_payroll.total_net
        else:
            # Fallback to latest run if current month not initialized
            latest_run = PayrollRun.objects.filter(tenant=tenant).order_by('-year', '-month').first()
            if latest_run:
                payroll_this_month = latest_run.total_gross
                net_salary_payable = latest_run.total_net

        # HR Alerts Panel
        alerts = []
        # Check unapproved leaves
        pending_leaves = LeaveApplication.objects.filter(tenant=tenant, status='pending').count()
        if pending_leaves > 0:
            alerts.append({
                "type": "warning",
                "title": "Pending Leave Requests",
                "message": f"{pending_leaves} leave application(s) awaiting manager/HR approval.",
                "action_url": "/hr/leave"
            })

        # Check pending overtime records
        pending_ot = OvertimeRecord.objects.filter(tenant=tenant, status='pending').count()
        if pending_ot > 0:
            alerts.append({
                "type": "info",
                "title": "Unapproved Overtime",
                "message": f"{pending_ot} overtime record(s) pending approval for upcoming payroll.",
                "action_url": "/hr/attendance"
            })

        # Check critical payroll exceptions
        critical_exceptions_qs = PayrollException.objects.filter(
            tenant=tenant,
            severity='critical',
            is_resolved=False
        ).select_related('employee')
        critical_count = critical_exceptions_qs.count()
        if critical_count > 0:
            alerts.append({
                "type": "danger",
                "title": "Critical Payroll Exceptions",
                "message": f"{critical_count} critical exception(s) blocking payroll approval.",
                "action_url": "/hr/payroll",
                "items": [
                    {
                        "id": str(ex.id),
                        "exception_id": str(ex.id),
                        "employee_id": str(ex.employee.id) if ex.employee else None,
                        "name": ex.employee.full_name if ex.employee else "General",
                        "employee_name": ex.employee.full_name if ex.employee else "General",
                        "employee_code": ex.employee.employee_code if ex.employee else "",
                        "code": ex.code,
                        "message": ex.message,
                    }
                    for ex in critical_exceptions_qs[:25]
                ]
            })

        # Check employees without bank details
        missing_bank_qs = Employee.objects.filter(
            tenant=tenant,
            status='active'
        ).filter(
            Q(bank_account_number='') | Q(bank_ifsc='') | Q(bank_account_number__isnull=True) | Q(bank_ifsc__isnull=True)
        ).select_related('department')
        missing_bank_count = missing_bank_qs.count()
        if missing_bank_count > 0:
            alerts.append({
                "type": "warning",
                "title": "Missing Bank Details",
                "message": f"{missing_bank_count} active employee(s) have incomplete bank or IFSC information.",
                "action_url": "/hr/employees",
                "items": [
                    {
                        "id": str(emp.id),
                        "employee_id": str(emp.id),
                        "name": emp.full_name,
                        "employee_name": emp.full_name,
                        "employee_code": emp.employee_code,
                        "department": emp.department.name if emp.department else "",
                        "bank_name": emp.bank_name or "",
                        "bank_ifsc": emp.bank_ifsc or "",
                        "bank_account_number": emp.bank_account_number or "",
                    }
                    for emp in missing_bank_qs[:25]
                ]
            })

        # Check employees without salary structure (exclude contract employees!)
        assigned_emp_ids = EmployeeSalaryAssignment.objects.filter(tenant=tenant).values_list('employee_id', flat=True)
        missing_salary_qs = Employee.objects.filter(tenant=tenant, status='active').exclude(employment_type='contract').exclude(id__in=assigned_emp_ids).select_related('department')
        missing_salary_count = missing_salary_qs.count()
        if missing_salary_count > 0:
            alerts.append({
                "type": "warning",
                "title": "Missing Salary Structures",
                "message": f"{missing_salary_count} active employee(s) do not have an assigned salary structure.",
                "action_url": "/hr/employees",
                "items": [
                    {
                        "id": str(emp.id),
                        "employee_id": str(emp.id),
                        "name": emp.full_name,
                        "employee_name": emp.full_name,
                        "employee_code": emp.employee_code,
                        "department": emp.department.name if emp.department else "",
                    }
                    for emp in missing_salary_qs[:25]
                ]
            })

        current_payroll_data = None
        if current_payroll:
            current_payroll_data = PayrollRunSerializer(current_payroll).data

        data = {
            "total_active_employees": active_employees,
            "on_leave_today": on_leave_today,
            "present_today": present_today,
            "last_payroll_net": str(net_salary_payable),
            "kpis": {
                "total_employees": total_employees,
                "active_employees": active_employees,
                "present_today": present_today,
                "on_leave_today": on_leave_today,
                "payroll_this_month": str(payroll_this_month),
                "net_salary_payable": str(net_salary_payable),
                "pending_payroll": pending_payroll_runs
            },
            "current_payroll": current_payroll_data,
            "alerts": alerts
        }
        
        return Response(data, status=status.HTTP_200_OK)


class SetupDefaultsView(APIView):
    """
    Seed default Departments, Designations and Leave Types for a tenant.
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def post(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)

        default_departments = ["Engineering", "Sales", "Human Resources", "Finance", "Operations"]
        default_designations = [
            "Software Engineer", "Senior Software Engineer", "Engineering Manager",
            "Sales Executive", "Sales Manager",
            "HR Executive", "HR Manager",
            "Accountant", "Finance Manager",
            "Operations Executive", "Operations Manager",
            "Director", "CEO"
        ]
        default_leave_types = [
            {"name": "Casual Leave",    "annual_entitlement": 12, "is_paid": True},
            {"name": "Sick Leave",      "annual_entitlement": 12, "is_paid": True},
            {"name": "Earned Leave",    "annual_entitlement": 15, "is_paid": True},
            {"name": "Maternity Leave", "annual_entitlement": 180, "is_paid": True},
            {"name": "Paternity Leave", "annual_entitlement": 15, "is_paid": True},
            {"name": "Loss of Pay",    "annual_entitlement": 0, "is_paid": False},
        ]

        with transaction.atomic():
            created_depts = 0
            for name in default_departments:
                dept, created = Department.objects.get_or_create(tenant=tenant, name=name)
                if created:
                    created_depts += 1
                    audit_service.log_create(request, dept, changes={'name': name})

            created_desigs = 0
            for name in default_designations:
                desig, created = Designation.objects.get_or_create(tenant=tenant, name=name)
                if created:
                    created_desigs += 1
                    audit_service.log_create(request, desig, changes={'name': name})

            created_leave_types = 0
            for lt_data in default_leave_types:
                lt, created = LeaveType.objects.get_or_create(
                    tenant=tenant,
                    name=lt_data["name"],
                    defaults={
                        "annual_entitlement": lt_data["annual_entitlement"],
                        "is_paid": lt_data["is_paid"],
                    }
                )
                if created:
                    created_leave_types += 1
                    audit_service.log_create(request, lt, changes={'name': lt.name})

        return Response({
            "status": "success",
            "departments_created": created_depts,
            "designations_created": created_desigs,
            "leave_types_created": created_leave_types,
        }, status=status.HTTP_201_CREATED)

from .models import EmployeeTask, EmployeeQuery
from .serializers import EmployeeTaskSerializer, EmployeeQuerySerializer

class EmployeeTaskViewSet(viewsets.ModelViewSet):
    serializer_class = EmployeeTaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            return EmployeeTask.objects.filter(employee__user=user)
        tenant = getattr(user, 'active_tenant', user)
        return EmployeeTask.objects.filter(employee__tenant=tenant)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'employee':
            raise ValidationError("Employees cannot create tasks.")
        
        instance = serializer.save(assigned_by=user)
        
        if instance.employee.personal_email:
            from hr.tasks import send_task_assignment_email
            from django.conf import settings
            use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
            if use_celery:
                send_task_assignment_email.delay(instance.employee.personal_email, instance.employee.full_name, instance.title)
            else:
                send_task_assignment_email(instance.employee.personal_email, instance.employee.full_name, instance.title)

    def perform_update(self, serializer):
        user = self.request.user
        if user.role == 'employee':
            # Employees can update status to in_progress or completed
            status_val = serializer.validated_data.get('status')
            if status_val in ['in_progress', 'completed']:
                if status_val == 'completed':
                    from django.utils import timezone
                    serializer.save(completed_at=timezone.now())
                else:
                    serializer.save()
            else:
                raise ValidationError("Employees can only mark tasks as in progress or completed.")
        else:
            serializer.save()

class EmployeeQueryViewSet(viewsets.ModelViewSet):
    serializer_class = EmployeeQuerySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'employee':
            return EmployeeQuery.objects.filter(employee__user=user)
        tenant = getattr(user, 'active_tenant', user)
        return EmployeeQuery.objects.filter(employee__tenant=tenant)

    def perform_create(self, serializer):
        user = self.request.user
        if user.role != 'employee':
            raise ValidationError("Only employees can create queries.")
        
        try:
            employee = Employee.objects.get(user=user)
        except Employee.DoesNotExist:
            raise ValidationError("Employee profile not found.")
            
        serializer.save(employee=employee, status='pending')

    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
        if user.role == 'employee':
            raise ValidationError("Employees cannot update queries.")
            
        updated_instance = serializer.save(resolved_by=user)
        
        if updated_instance.status in ['resolved', 'rejected'] and instance.status != updated_instance.status:
            if updated_instance.employee.personal_email:
                from hr.tasks import send_query_resolution_email
                from django.conf import settings
                use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
                if use_celery:
                    send_query_resolution_email.delay(updated_instance.employee.personal_email, updated_instance.employee.full_name, updated_instance.subject, updated_instance.status)
                else:
                    send_query_resolution_email(updated_instance.employee.personal_email, updated_instance.employee.full_name, updated_instance.subject, updated_instance.status)


from django.db.models import Q
from .models import EmployeeNotification
from .serializers import EmployeeNotificationSerializer

class EmployeeNotificationViewSet(viewsets.ModelViewSet):
    serializer_class = EmployeeNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        
        if user.role == 'employee':
            try:
                employee = Employee.objects.get(user=user)
                return EmployeeNotification.objects.filter(
                    tenant=tenant
                ).filter(
                    Q(employee__isnull=True) | Q(employee=employee)
                )
            except Employee.DoesNotExist:
                return EmployeeNotification.objects.none()
                
        return EmployeeNotification.objects.filter(tenant=tenant)

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        
        if user.role == 'employee':
            raise ValidationError("Employees cannot send notifications.")
            
        instance = serializer.save(tenant=tenant, created_by=user)
        
        # Dispatch emails
        from django.conf import settings
        from hr.tasks import send_via_integration
        
        title = instance.title
        message = instance.message
        
        def get_html_notification(to_name, announcement_title, announcement_message):
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{
                        font-family: 'Outfit', 'Inter', -apple-system, sans-serif;
                        background-color: #08090c;
                        color: #e2e8f0;
                        margin: 0;
                        padding: 0;
                    }}
                    .container {{
                        max-width: 600px;
                        margin: 40px auto;
                        background: linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 24px;
                        padding: 40px;
                        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
                    }}
                    .logo {{
                        font-size: 24px;
                        font-weight: 800;
                        background: linear-gradient(to right, #22d3ee, #6366f1);
                        -webkit-background-clip: text;
                        -webkit-text-fill-color: transparent;
                        margin-bottom: 30px;
                        letter-spacing: 1px;
                        text-align: center;
                    }}
                    .congrats-title {{
                        font-size: 22px;
                        font-weight: 700;
                        color: #ffffff;
                        margin-bottom: 20px;
                        border-left: 4px solid #6366f1;
                        padding-left: 12px;
                    }}
                    .welcome-text {{
                        font-size: 15px;
                        line-height: 1.6;
                        color: #94a3b8;
                        margin-bottom: 30px;
                        white-space: pre-wrap;
                    }}
                    .footer {{
                        margin-top: 40px;
                        font-size: 12px;
                        color: #475569;
                        border-top: 1px solid rgba(255, 255, 255, 0.05);
                        padding-top: 20px;
                        text-align: center;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="logo">CENVORA</div>
                    <div class="congrats-title">{announcement_title}</div>
                    <div class="welcome-text">
Hello {to_name},

An official notification has been broadcasted by the HR/Admin team:

{announcement_message}
                    </div>
                    <div class="footer">
                        &copy; 2026 Cenvora App. All rights reserved. Sent securely on behalf of {tenant.business_name or 'HR Team'}.
                    </div>
                </div>
            </body>
            </html>
            """
            
        if instance.employee:
            if instance.employee.personal_email:
                html = get_html_notification(instance.employee.full_name, title, message)
                send_via_integration(instance.employee.personal_email, title, message, html_body=html)
        else:
            # Broadcast to all active employees
            active_emps = Employee.objects.filter(tenant=tenant, status='active')
            for emp in active_emps:
                if emp.personal_email:
                    html = get_html_notification(emp.full_name, title, message)
                    send_via_integration(emp.personal_email, title, message, html_body=html)


class EmployeeSalaryHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only viewset for immutable audit trail of employee salary increments/revisions.
    """
    serializer_class = EmployeeSalaryHistorySerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        qs = EmployeeSalaryHistory.objects.filter(employee__tenant=tenant)
        if user.role == 'employee':
            try:
                emp = Employee.objects.get(tenant=tenant, user=user)
                return qs.filter(employee=emp)
            except Employee.DoesNotExist:
                return qs.none()
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs


class OvertimeRecordViewSet(viewsets.ModelViewSet):
    """
    ViewSet for logging and approving overtime hours.
    """
    serializer_class = OvertimeRecordSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        qs = OvertimeRecord.objects.filter(tenant=tenant)
        if user.role == 'employee':
            try:
                emp = Employee.objects.get(tenant=tenant, user=user)
                return qs.filter(employee=emp)
            except Employee.DoesNotExist:
                return qs.none()
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        ot = self.get_object()
        if ot.status != 'pending':
            return Response({"error": f"Cannot approve overtime with status {ot.status}."}, status=status.HTTP_400_BAD_REQUEST)
        ot.status = 'approved'
        ot.approved_by = request.user
        ot.save()
        return Response({"status": "approved"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        ot = self.get_object()
        if ot.status != 'pending':
            return Response({"error": f"Cannot reject overtime with status {ot.status}."}, status=status.HTTP_400_BAD_REQUEST)
        ot.status = 'rejected'
        ot.approved_by = request.user
        ot.save()
        return Response({"status": "rejected"}, status=status.HTTP_200_OK)


class EmployeeAdvanceLoanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for tracking employee salary advances and personal loans with recovery tracking.
    """
    serializer_class = EmployeeAdvanceLoanSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        qs = EmployeeAdvanceLoan.objects.filter(tenant=tenant)
        if user.role == 'employee':
            try:
                emp = Employee.objects.get(tenant=tenant, user=user)
                return qs.filter(employee=emp)
            except Employee.DoesNotExist:
                return qs.none()
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        loan_type = self.request.query_params.get('type')
        if loan_type:
            qs = qs.filter(record_type=loan_type)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        loan = self.get_object()
        loan.status = 'active'
        loan.disbursed_by = request.user
        loan.save()
        return Response({"status": "active"}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        loan = self.get_object()
        loan.status = 'closed'
        loan.outstanding_balance = Decimal('0.00')
        loan.save()
        return Response({"status": "closed"}, status=status.HTTP_200_OK)


class PayrollExceptionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing pre-approval payroll exceptions and warnings.
    """
    serializer_class = PayrollExceptionSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        qs = PayrollException.objects.filter(tenant=tenant)
        payroll_run_id = self.request.query_params.get('payroll_run')
        if payroll_run_id:
            qs = qs.filter(payroll_run_id=payroll_run_id)
        return qs

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        exc = self.get_object()
        exc.is_resolved = True
        exc.resolved_by = request.user
        exc.save()
        return Response({"status": "resolved"}, status=status.HTTP_200_OK)


class HRDocumentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing employee HR documents (offer letters, ID proof, contracts).
    """
    serializer_class = HRDocumentSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        qs = HRDocument.objects.filter(tenant=tenant)
        if user.role == 'employee':
            try:
                emp = Employee.objects.get(tenant=tenant, user=user)
                return qs.filter(employee=emp)
            except Employee.DoesNotExist:
                return qs.none()
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        tenant = getattr(user, 'active_tenant', user)
        try:
            self.perform_create(serializer)
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as DRFValidationError
            if isinstance(exc, (ValidationError, DRFValidationError)):
                raise
            return Response(
                {'detail': f'Could not save document: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_create(self, serializer):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        serializer.save(tenant=tenant, uploaded_by=user)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        Authenticated download. Remote Cloudinary files redirect; local files stream.
        Only tenant HR/Admin or the owner employee can access the document.
        """
        doc = self.get_object()
        remote_url = doc.absolute_file_url
        if remote_url.startswith(('http://', 'https://')):
            from django.shortcuts import redirect
            return redirect(remote_url)
        if not doc.file:
            if doc.file_url:
                from django.shortcuts import redirect
                return redirect(doc.file_url)
            return Response({'error': 'No file attached to this document record.'}, status=status.HTTP_404_NOT_FOUND)

        from django.http import FileResponse
        try:
            response = FileResponse(doc.file.open('rb'))
            response['Content-Disposition'] = f'inline; filename="{doc.file.name.split("/")[-1]}"'
            return response
        except Exception as e:
            return Response({'error': f'Failed to retrieve file: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class HRMSSettingsView(APIView):
    """
    Get or update global HRMS Settings for the tenant.
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        settings_obj = get_hrms_settings(tenant)
        serializer = HRMSSettingsSerializer(settings_obj)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        settings_obj = get_hrms_settings(tenant)
        serializer = HRMSSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class HRReportsView(APIView):
    """
    Consolidated HR reports:
    - Payroll Register
    - Salary Sheet
    - Department Expenses
    - Branch Expenses
    - Statutory Summary (PF / ESI / PT / TDS)
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        report_type = request.query_params.get('type', 'payroll_register')
        year = int(request.query_params.get('year', timezone.now().year))
        month = int(request.query_params.get('month', timezone.now().month))

        if report_type == 'payroll_register':
            payslips = Payslip.objects.filter(
                payroll_run__tenant=tenant,
                payroll_run__year=year,
                payroll_run__month=month
            ).select_related('employee', 'employee__department', 'employee__branch', 'employee__designation', 'payroll_run')

            records = []
            for p in payslips:
                records.append({
                    "payslip_id": str(p.id),
                    "employee_id": str(p.employee.id),
                    "employee_code": p.employee.employee_code,
                    "employee_name": p.employee.full_name,
                    "designation": p.employee.designation.name if p.employee.designation else '—',
                    "department": p.employee.department.name if p.employee.department else '—',
                    "branch": p.employee.branch.name if p.employee.branch else 'Head Office',
                    "employment_type": p.employee.employment_type or 'full_time',
                    "date_of_joining": str(p.employee.date_of_joining or ''),
                    "work_state": p.employee.work_state or '—',
                    "personal_email": p.employee.personal_email or '',
                    "personal_phone": p.employee.personal_phone or '',
                    "bank_name": p.employee.bank_name or '—',
                    "bank_account": p.employee.bank_account_number or '—',
                    "ifsc": p.employee.bank_ifsc or '—',
                    "upi_id": p.employee.upi_id or '',
                    "pan": p.employee.pan_number or '—',
                    "uan": getattr(p.employee, 'uan', None) or '—',
                    "aadhaar": getattr(p.employee, 'aadhaar_number', None) or '—',
                    "esi_ip_number": getattr(p.employee, 'esi_ip_number', None) or '—',
                    "tax_regime": getattr(p.employee, 'tax_regime', 'new') or 'new',
                    "working_days": str(p.total_working_days),
                    "present_days": str(p.present_days),
                    "absent_days": str(p.absent_days),
                    "paid_leave_days": str(p.paid_leave_days),
                    "lop_days": str(p.lop_days),
                    "overtime_hours": str(p.overtime_hours),
                    "overtime_amount": str(p.overtime_amount),
                    "gross_salary": str(p.gross_salary),
                    "earnings": p.earnings or {},
                    "deductions": p.deductions or {},
                    "deduction_reasons": p.deduction_reasons or {},
                    "employee_pf": str(p.employee_pf),
                    "employee_esi": str(p.employee_esi),
                    "professional_tax": str(p.professional_tax),
                    "tds": str(p.tds),
                    "advance_recovery": str(p.advance_recovery),
                    "loan_recovery": str(p.loan_recovery),
                    "total_deductions": str(p.total_deductions),
                    "employer_pf": str(p.employer_pf),
                    "employer_epf": str(p.employer_epf),
                    "employer_eps": str(p.employer_eps),
                    "employer_esi": str(p.employer_esi),
                    "employer_contribution": str(p.employer_total_contribution),
                    "net_salary": str(p.net_salary),
                    "status": p.payroll_run.status,
                    "payroll_run_id": str(p.payroll_run.id),
                })
            return Response({"year": year, "month": month, "count": len(records), "records": records}, status=status.HTTP_200_OK)

        elif report_type == 'department_expenses':
            payslips = Payslip.objects.filter(
                payroll_run__tenant=tenant,
                payroll_run__year=year,
                payroll_run__month=month
            ).select_related('employee__department')

            dept_map = {}
            for p in payslips:
                dept_name = p.employee.department.name if p.employee.department else 'Unassigned'
                if dept_name not in dept_map:
                    dept_map[dept_name] = {
                        "department": dept_name,
                        "headcount": 0,
                        "gross": Decimal('0.00'),
                        "net": Decimal('0.00'),
                        "deductions": Decimal('0.00'),
                        "employer_contribution": Decimal('0.00')
                    }
                dept_map[dept_name]["headcount"] += 1
                dept_map[dept_name]["gross"] += p.gross_salary
                dept_map[dept_name]["net"] += p.net_salary
                dept_map[dept_name]["deductions"] += p.total_deductions
                dept_map[dept_name]["employer_contribution"] += p.employer_total_contribution

            results = [
                {
                    "department": v["department"],
                    "headcount": v["headcount"],
                    "gross": str(v["gross"]),
                    "net": str(v["net"]),
                    "deductions": str(v["deductions"]),
                    "employer_contribution": str(v["employer_contribution"]),
                }
                for v in dept_map.values()
            ]
            return Response({"year": year, "month": month, "results": results}, status=status.HTTP_200_OK)

        elif report_type == 'statutory_summary':
            payslips = Payslip.objects.filter(
                payroll_run__tenant=tenant,
                payroll_run__year=year,
                payroll_run__month=month
            )
            total_pf = Decimal('0.00')
            total_esi = Decimal('0.00')
            total_pt = Decimal('0.00')
            total_tds = Decimal('0.00')
            total_employer_pf = Decimal('0.00')
            total_employer_esi = Decimal('0.00')

            for p in payslips:
                total_pf += p.employee_pf
                total_esi += p.employee_esi
                total_pt += p.professional_tax
                total_tds += p.tds
                total_employer_pf += p.employer_pf
                total_employer_esi += p.employer_esi

            return Response({
                "year": year,
                "month": month,
                "employee_pf": str(total_pf),
                "employer_pf": str(total_employer_pf),
                "total_pf_liability": str(total_pf + total_employer_pf),
                "employee_esi": str(total_esi),
                "employer_esi": str(total_employer_esi),
                "total_esi_liability": str(total_esi + total_employer_esi),
                "professional_tax": str(total_pt),
                "tds_payable": str(total_tds),
            }, status=status.HTTP_200_OK)

        elif report_type == 'pt_register':
            # Read-only PT return / payment register by work state
            payslips = Payslip.objects.filter(
                payroll_run__tenant=tenant,
                payroll_run__year=year,
                payroll_run__month=month,
            ).select_related('employee', 'payroll_run')
            by_state = {}
            lines = []
            total_pt = Decimal('0.00')
            for p in payslips:
                state = p.employee.work_state or 'Unspecified'
                pt = p.professional_tax or Decimal('0.00')
                total_pt += pt
                by_state.setdefault(state, Decimal('0.00'))
                by_state[state] += pt
                lines.append({
                    'employee_code': p.employee.employee_code,
                    'employee_name': p.employee.full_name,
                    'work_state': state,
                    'gross_salary': str(p.gross_salary),
                    'professional_tax': str(pt),
                })
            return Response({
                'year': year,
                'month': month,
                'total_pt_payable': str(total_pt),
                'by_state': {k: str(v) for k, v in by_state.items()},
                'lines': lines,
                'count': len(lines),
            }, status=status.HTTP_200_OK)

        return Response({"error": "Unknown report type."}, status=status.HTTP_400_BAD_REQUEST)


class EmployeeAllowanceBonusViewSet(viewsets.ModelViewSet):
    """
    ViewSet for tracking and managing employee allowances and bonuses.
    """
    serializer_class = EmployeeAllowanceBonusSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        user = self.request.user
        tenant = getattr(user, 'active_tenant', user)
        if user.role == 'employee':
            return EmployeeAllowanceBonus.objects.filter(employee__user=user).select_related('employee')
        qs = EmployeeAllowanceBonus.objects.filter(tenant=tenant).select_related('employee')
        rec_type = self.request.query_params.get('type')
        if rec_type:
            qs = qs.filter(record_type=rec_type)
        emp_id = self.request.query_params.get('employee')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'amount': str(instance.amount), 'record_type': instance.record_type})

    def perform_update(self, serializer):
        instance = serializer.save()
        audit_service.log_update(self.request, instance, after={'amount': str(instance.amount)})

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        item = self.get_object()
        item.status = 'approved'
        item.save(update_fields=['status', 'updated_at'])
        return Response(self.get_serializer(item).data, status=status.HTTP_200_OK)


class EmployeeTaxDeclarationViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for EmployeeTaxDeclaration under Section 192.
    """
    serializer_class = EmployeeTaxDeclarationSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        qs = EmployeeTaxDeclaration.objects.filter(tenant=tenant)
        employee_id = self.request.query_params.get('employee')
        if employee_id:
            qs = qs.filter(employee_id=employee_id)
        fy = self.request.query_params.get('financial_year')
        if fy:
            qs = qs.filter(financial_year=fy)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        instance = serializer.save(tenant=tenant)
        audit_service.log_create(self.request, instance, changes={'employee': str(instance.employee_id), 'financial_year': instance.financial_year})

    def perform_update(self, serializer):
        instance = serializer.save()
        audit_service.log_update(self.request, instance, after={'regime': instance.regime, 'proof_verified': instance.proof_verified})

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()


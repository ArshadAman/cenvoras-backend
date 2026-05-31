# HR DRF ViewSets and function-based views — implemented in tasks 6–16

from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from django.db import transaction

from .models import (
    Department, Designation, Employee, AttendanceRecord,
    LeaveType, LeaveBalance, LeaveApplication,
    SalaryStructure, SalaryComponent, EmployeeSalaryAssignment,
    PayrollRun, Payslip
)
from .serializers import (
    DepartmentSerializer, DesignationSerializer, EmployeeSerializer,
    AttendanceRecordSerializer, LeaveTypeSerializer, LeaveBalanceSerializer,
    LeaveApplicationSerializer, SalaryStructureSerializer, EmployeeSalaryAssignmentSerializer,
    PayrollRunSerializer, PayslipSerializer
)
from .permissions import HRPermission
from .services import audit_service, leave_service


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

        audit_service.log_create(
            self.request,
            instance,
            changes={
                'employee_code': instance.employee_code,
                'full_name': instance.full_name,
                'status': instance.status,
            },
        )

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

    def perform_destroy(self, instance):
        audit_service.log_delete(self.request, instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def increment_salary(self, request, pk=None):
        instance = self.get_object()
        tenant = getattr(request.user, 'active_tenant', request.user)
        
        increment_percentage = request.data.get('increment_percentage')
        effective_from = request.data.get('effective_from')
        
        if not increment_percentage or not effective_from:
            raise ValidationError("increment_percentage and effective_from are required.")
            
        try:
            increment_percentage = float(increment_percentage)
        except ValueError:
            raise ValidationError("increment_percentage must be a number.")
            
        latest_assignment = EmployeeSalaryAssignment.objects.filter(employee=instance).order_by('-effective_from').first()
        if not latest_assignment:
            raise ValidationError("Employee has no existing salary assignment.")
            
        from decimal import Decimal
        new_ctc = latest_assignment.monthly_ctc * Decimal(1 + (increment_percentage / 100.0))
        new_ctc = round(new_ctc, 2)
        
        data = {
            'employee': instance.id,
            'salary_structure': latest_assignment.salary_structure.id,
            'effective_from': effective_from,
            'monthly_ctc': new_ctc
        }
        
        # We must import the serializer locally to avoid circular imports or just use it if already imported
        from hr.serializers import EmployeeSalaryAssignmentSerializer
        serializer = EmployeeSalaryAssignmentSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        new_assignment = serializer.save(tenant=tenant)
        
        if instance.personal_email:
            from hr.tasks import send_salary_increment_email
            from django.conf import settings
            use_celery = bool(getattr(settings, 'CELERY_BROKER_URL', None)) and not getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False)
            if use_celery:
                send_salary_increment_email.delay(instance.personal_email, instance.full_name, str(new_ctc))
            else:
                send_salary_increment_email(instance.personal_email, instance.full_name, str(new_ctc))
                
        return Response({'status': 'Salary incremented', 'new_ctc': new_ctc}, status=status.HTTP_200_OK)


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
            return AttendanceRecord.objects.filter(employee__user=user).select_related('employee').order_by('-date')
        tenant = getattr(user, 'active_tenant', user)
        return AttendanceRecord.objects.filter(tenant=tenant).select_related('employee').order_by('-date')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request.user, 'active_tenant', request.user)
        employee = serializer.validated_data['employee']
        date = serializer.validated_data['date']
        att_status = serializer.validated_data['status']

        record = AttendanceRecord.objects.filter(employee=employee, date=date).first()
        if not record:
            record = AttendanceRecord.objects.create(tenant=tenant, employee=employee, date=date, status=att_status)
            audit_service.log_create(request, record, changes={'status': att_status})
            return Response(self.get_serializer(record).data, status=status.HTTP_201_CREATED)
        else:
            before = {'status': record.status}
            record.status = att_status
            record.save()
            audit_service.log_update(request, record, before=before, after={'status': att_status})
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
        
        computed_days = leave_service.compute_leave_days(start_date, end_date, employee)
        
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
            # Run synchronously in local dev — task closes the DB connection on exit,
            # so we reset it before doing anything with the DB afterwards.
            run_payroll_task(str(instance.id))
            close_old_connections()

            # Re-fetch the instance on the fresh connection so we get the real status
            instance.refresh_from_db()
            try:
                audit_service.log_update(request, instance, before={'status': 'processing'}, after={'status': instance.status})
            except Exception:
                pass  # Non-critical; don't fail the whole request over an audit log

            return Response({'status': instance.status}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def finalise(self, request, pk=None):
        instance = self.get_object()
        
        if instance.status != 'completed':
            raise ValidationError("Only completed payroll runs can be finalised.")
            
        before = {'status': instance.status, 'finalised_at': str(instance.finalised_at)}
        instance.status = 'finalised'
        instance.finalised_at = timezone.now()
        instance.save(update_fields=['status', 'finalised_at'])
        
        after = {'status': instance.status, 'finalised_at': str(instance.finalised_at)}
        audit_service.log_update(request, instance, before=before, after=after)
        return Response({'status': 'Payroll run finalised'}, status=status.HTTP_200_OK)


class PayslipViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for Payslip.
    Requirements: 10.1, 12.1
    """
    serializer_class = PayslipSerializer
    permission_classes = [permissions.IsAuthenticated, HRPermission]
    payroll_action = True

    def get_queryset(self):
        tenant = getattr(self.request.user, 'active_tenant', self.request.user)
        return Payslip.objects.filter(tenant=tenant)


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

        pdf_bytes = generate_payslip_pdf(payslip)
        
        audit_service.log_download(request, payslip)
        
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        filename = f"payslip_{payslip.employee.employee_code}_{payslip.payroll_run.month}_{payslip.payroll_run.year}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response


class HRDashboardView(APIView):
    """
    Dashboard metrics for HR module.
    Requirements: 11.1, 11.2, 11.3, 11.4
    """
    permission_classes = [permissions.IsAuthenticated, HRPermission]

    def get(self, request, *args, **kwargs):
        tenant = getattr(request.user, 'active_tenant', request.user)
        cache_key = f"hr_dashboard_{tenant.id}"
        
        cached_data = cache.get(cache_key)
        if cached_data:
            return Response(cached_data, status=status.HTTP_200_OK)
            
        import pytz
        tz = pytz.timezone('Asia/Kolkata')
        today = timezone.now().astimezone(tz).date()
        
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
        
        latest_run = PayrollRun.objects.filter(tenant=tenant, status='finalised').order_by('-finalised_at').first()
        last_payroll_net = str(latest_run.total_net) if latest_run else None
        
        data = {
            "total_active_employees": active_employees,
            "on_leave_today": on_leave_today,
            "present_today": present_today,
            "last_payroll_net": last_payroll_net
        }
        
        cache.set(cache_key, data, timeout=300)
        
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
            # Employees can only update status to completed
            if 'status' in serializer.validated_data and serializer.validated_data['status'] == 'completed':
                from django.utils import timezone
                serializer.save(completed_at=timezone.now())
            else:
                raise ValidationError("Employees can only mark tasks as completed.")
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
                        &copy; 2026 Cenvora Cloud. All rights reserved. Sent securely on behalf of {tenant.business_name or 'HR Team'}.
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

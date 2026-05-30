from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    DepartmentViewSet, DesignationViewSet, EmployeeViewSet, 
    AttendanceViewSet, BulkAttendanceView,
    LeaveTypeViewSet, LeaveBalanceViewSet, LeaveApplicationViewSet,
    LeaveApproveView, LeaveRejectView,
    SalaryStructureViewSet, SalaryAssignmentViewSet,
    PayrollRunViewSet, PayslipViewSet,
    PayslipPDFView, HRDashboardView, SetupDefaultsView,
    EmployeeTaskViewSet, EmployeeQueryViewSet
)

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
router.register(r'tasks', EmployeeTaskViewSet, basename='task')
router.register(r'queries', EmployeeQueryViewSet, basename='query')

urlpatterns = [
    path('attendance/bulk/', BulkAttendanceView.as_view(), name='bulk-attendance'),
    path('leave-applications/<uuid:pk>/approve/', LeaveApproveView.as_view(), name='leave-approve'),
    path('leave-applications/<uuid:pk>/reject/', LeaveRejectView.as_view(), name='leave-reject'),
    path('payslips/<uuid:pk>/pdf/', PayslipPDFView.as_view(), name='payslip-pdf'),
    path('dashboard/', HRDashboardView.as_view(), name='hr-dashboard'),
    path('setup-defaults/', SetupDefaultsView.as_view(), name='setup-defaults'),
    path('', include(router.urls)),
]

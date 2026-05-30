from celery import shared_task
from hr.models import PayrollRun
from hr.services.payroll_engine import run_payroll

@shared_task
def run_payroll_task(payroll_run_id):
    try:
        run = PayrollRun.objects.get(id=payroll_run_id)
        # Ensure status reflects processing initially if needed, though ViewSet usually sets it.
        run_payroll(payroll_run_id)
        run.refresh_from_db()
        run.status = 'completed'
        run.save(update_fields=['status'])
    except Exception as e:
        try:
            run = PayrollRun.objects.get(id=payroll_run_id)
            run.status = 'draft'
            run.save(update_fields=['status'])
            # No error_note field on model, so logging is sufficient or custom note handling.
            print(f"Payroll run {payroll_run_id} failed: {str(e)}")
        except PayrollRun.DoesNotExist:
            pass

from django.conf import settings
from hr.models import Employee
from integration.tasks import send_async_email_notification

def send_via_integration(email, subject, message):
    try:
        emp = Employee.objects.filter(personal_email=email).first()
        if emp:
            send_async_email_notification.delay(
                str(emp.tenant.id),
                email,
                subject,
                message,
                'Employee',
                str(emp.id)
            )
        else:
            print(f"Could not find employee for email: {email}")
    except Exception as e:
        print(f"Error dispatching email: {e}")

@shared_task
def send_employee_account_creation_email(employee_email, name, password, business_name):
    subject = f"Welcome to {business_name} - Your Account Credentials"
    message = f"Hi {name},\n\nYour account has been created successfully.\n\nLogin Email: {employee_email}\nPassword: {password}\n\nPlease log in and change your password.\n\nBest,\nHR Team"
    send_via_integration(employee_email, subject, message)

@shared_task
def send_task_assignment_email(employee_email, name, task_title):
    subject = "New Task Assigned"
    message = f"Hi {name},\n\nYou have been assigned a new task: '{task_title}'. Please log in to your portal to view details.\n\nBest,\nHR Team"
    send_via_integration(employee_email, subject, message)

@shared_task
def send_query_resolution_email(employee_email, name, subject, status):
    email_subject = f"Query Status Updated: {subject}"
    message = f"Hi {name},\n\nYour query '{subject}' has been marked as {status}.\n\nBest,\nHR Team"
    send_via_integration(employee_email, email_subject, message)

@shared_task
def send_salary_increment_email(employee_email, name, new_ctc):
    subject = "Salary Increment Notification"
    message = f"Hi {name},\n\nCongratulations! Your salary has been updated. Your new monthly CTC is {new_ctc}.\n\nBest,\nHR Team"
    send_via_integration(employee_email, subject, message)

@shared_task
def send_leave_status_email(employee_email, name, leave_type, start_date, end_date, status):
    subject = f"Leave Application {status.capitalize()}"
    message = f"Hi {name},\n\nYour leave application for {leave_type} from {start_date} to {end_date} has been {status}.\n\nBest,\nHR Team"
    send_via_integration(employee_email, subject, message)

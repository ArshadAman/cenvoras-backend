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

def send_via_integration(email, subject, message, html_body=None):
    try:
        emp = Employee.objects.filter(personal_email=email).first()
        if emp:
            send_async_email_notification.delay(
                str(emp.tenant.id),
                email,
                subject,
                message,
                'Employee',
                str(emp.id),
                html_body=html_body
            )
        else:
            print(f"Could not find employee for email: {email}")
    except Exception as e:
        print(f"Error dispatching email: {e}")

@shared_task
def send_employee_account_creation_email(employee_email, name, password, business_name):
    # Retrieve designation
    try:
        emp = Employee.objects.filter(personal_email=employee_email).first()
        designation = emp.designation.name if (emp and emp.designation) else "Team Member"
    except Exception:
        designation = "Team Member"

    subject = f"Welcome to {business_name} - Your Account Credentials"
    message = f"Hi {name},\n\nYour account has been created successfully.\n\nLogin Email: {employee_email}\nPassword: {password}\n\nPlease log in and change your password.\n\nBest,\nHR Team"
    
    html_body = f"""
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
                text-align: center;
            }}
            .logo {{
                font-size: 24px;
                font-weight: 800;
                background: linear-gradient(to right, #22d3ee, #6366f1);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 30px;
                letter-spacing: 1px;
            }}
            .congrats-title {{
                font-size: 28px;
                font-weight: 700;
                color: #ffffff;
                margin-bottom: 10px;
            }}
            .designation-badge {{
                display: inline-block;
                background: rgba(99, 102, 241, 0.15);
                color: #a5b4fc;
                border: 1px solid rgba(99, 102, 241, 0.3);
                border-radius: 50px;
                padding: 6px 16px;
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 24px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            .welcome-text {{
                font-size: 16px;
                line-height: 1.6;
                color: #94a3b8;
                margin-bottom: 30px;
            }}
            .credential-card {{
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 16px;
                padding: 24px;
                margin-bottom: 30px;
                text-align: left;
            }}
            .credential-title {{
                font-size: 12px;
                font-weight: 700;
                color: #64748b;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 12px;
            }}
            .credential-row {{
                margin-bottom: 12px;
                font-size: 15px;
            }}
            .credential-row:last-child {{
                margin-bottom: 0;
            }}
            .label {{
                color: #64748b;
                display: inline-block;
                width: 100px;
            }}
            .value {{
                color: #ffffff;
                font-family: monospace;
                font-weight: 600;
            }}
            .btn-primary {{
                display: inline-block;
                background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
                color: #0f172a !important;
                text-decoration: none;
                font-weight: 700;
                font-size: 16px;
                padding: 14px 32px;
                border-radius: 14px;
                margin-top: 10px;
                box-shadow: 0 10px 20px rgba(6, 182, 212, 0.2);
                transition: transform 0.2s, box-shadow 0.2s;
            }}
            .footer {{
                margin-top: 40px;
                font-size: 12px;
                color: #475569;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                padding-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">CENVORA</div>
            <div class="congrats-title">Congratulations, {name}!</div>
            <div class="designation-badge">{designation}</div>
            <div class="welcome-text">
                Welcome to <strong>{business_name}</strong>! We are absolutely thrilled to have you join our team. 
                Your workspace has been successfully initialized, and your secure portal credentials are ready below.
            </div>
            <div class="credential-card">
                <div class="credential-title">Secure Portal Access</div>
                <div class="credential-row">
                    <span class="label">Login Email:</span>
                    <span class="value">{employee_email}</span>
                </div>
                <div class="credential-row">
                    <span class="label">Password:</span>
                    <span class="value">{password}</span>
                </div>
            </div>
            <div style="margin: 30px 0;">
                <a href="https://cenvora.app/login" class="btn-primary">Access Your Portal</a>
            </div>
            <div class="welcome-text" style="font-size: 14px; margin-top: 20px;">
                <em>Note: For security reasons, please log in and update your password immediately upon access.</em>
            </div>
            <div class="footer">
                &copy; 2026 Cenvora Cloud. All rights reserved. Sent securely on behalf of {business_name} HR Team.
            </div>
        </div>
    </body>
    </html>
    """

    send_via_integration(employee_email, subject, message, html_body=html_body)

def get_beautiful_html_template(name, header_title, body_text, cta_url=None, cta_label=None, footer_text=None):
    cta_section = ""
    if cta_url and cta_label:
        cta_section = f"""
        <div style="margin: 30px 0;">
            <a href="{cta_url}" class="btn-primary">{cta_label}</a>
        </div>
        """
        
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
                text-align: center;
            }}
            .logo {{
                font-size: 24px;
                font-weight: 800;
                background: linear-gradient(to right, #22d3ee, #6366f1);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 30px;
                letter-spacing: 1px;
            }}
            .congrats-title {{
                font-size: 22px;
                font-weight: 700;
                color: #ffffff;
                margin-bottom: 20px;
            }}
            .welcome-text {{
                font-size: 15px;
                line-height: 1.6;
                color: #94a3b8;
                margin-bottom: 30px;
                text-align: left;
                white-space: pre-wrap;
            }}
            .btn-primary {{
                display: inline-block;
                background: linear-gradient(135deg, #06b6d4 0%, #3b82f6 100%);
                color: #0f172a !important;
                text-decoration: none;
                font-weight: 700;
                font-size: 16px;
                padding: 14px 32px;
                border-radius: 14px;
                margin-top: 10px;
                box-shadow: 0 10px 20px rgba(6, 182, 212, 0.2);
            }}
            .footer {{
                margin-top: 40px;
                font-size: 12px;
                color: #475569;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
                padding-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">CENVORA</div>
            <div class="congrats-title">{header_title}</div>
            <div class="welcome-text">
Hello {name},

{body_text}
            </div>
            {cta_section}
            <div class="footer">
                {footer_text or '&copy; 2026 Cenvora Cloud. All rights reserved.'}
            </div>
        </div>
    </body>
    </html>
    """

@shared_task
def send_task_assignment_email(employee_email, name, task_title):
    subject = "New Task Assigned"
    message = f"Hi {name},\n\nYou have been assigned a new task: '{task_title}'. Please log in to your portal to view details.\n\nBest,\nHR Team"
    body_text = f"You have been assigned a new task:\n\n<strong>{task_title}</strong>\n\nPlease log in to your portal to view details and update your progress."
    html_body = get_beautiful_html_template(
        name=name,
        header_title="New Task Assignment",
        body_text=body_text,
        cta_url="https://cenvora.app/login",
        cta_label="Access Employee Portal",
        footer_text="Sent securely on behalf of the HR Team."
    )
    send_via_integration(employee_email, subject, message, html_body=html_body)

@shared_task
def send_query_resolution_email(employee_email, name, subject, status):
    email_subject = f"Query Status Updated: {subject}"
    message = f"Hi {name},\n\nYour query '{subject}' has been marked as {status}.\n\nBest,\nHR Team"
    body_text = f"Your query has been updated:\n\nQuery Subject: <strong>{subject}</strong>\nStatus: <strong style='text-transform: uppercase; color: #10b981;'>{status}</strong>\n\nPlease log in to check any resolution remarks from HR."
    html_body = get_beautiful_html_template(
        name=name,
        header_title="Query Status Update",
        body_text=body_text,
        cta_url="https://cenvora.app/login",
        cta_label="View Employee Portal",
        footer_text="Sent securely on behalf of the HR Team."
    )
    send_via_integration(employee_email, email_subject, message, html_body=html_body)

@shared_task
def send_salary_increment_email(employee_email, name, new_ctc):
    subject = "Salary Increment Notification"
    message = f"Hi {name},\n\nCongratulations! Your salary has been updated. Your new monthly CTC is {new_ctc}.\n\nBest,\nHR Team"
    body_text = f"Congratulations! Your salary has been revised.\n\nNew Monthly CTC: <strong>\u20b9{new_ctc}</strong>\n\nWe deeply appreciate your contributions to the team's success!"
    html_body = get_beautiful_html_template(
        name=name,
        header_title="Salary Revision",
        body_text=body_text,
        cta_url="https://cenvora.app/login",
        cta_label="View Payslips & Portal",
        footer_text="Sent securely on behalf of the HR Team."
    )
    send_via_integration(employee_email, subject, message, html_body=html_body)

@shared_task
def send_leave_status_email(employee_email, name, leave_type, start_date, end_date, status):
    subject = f"Leave Application {status.capitalize()}"
    message = f"Hi {name},\n\nYour leave application for {leave_type} from {start_date} to {end_date} has been {status}.\n\nBest,\nHR Team"
    status_color = "#10b981" if status.lower() == 'approved' else "#ef4444"
    body_text = f"Your leave request has been reviewed:\n\nLeave Type: <strong>{leave_type}</strong>\nDuration: <strong>{start_date} to {end_date}</strong>\nStatus: <strong style='text-transform: uppercase; color: {status_color};'>{status}</strong>\n\nPlease log in to check your updated leave balances."
    html_body = get_beautiful_html_template(
        name=name,
        header_title=f"Leave Application {status.capitalize()}",
        body_text=body_text,
        cta_url="https://cenvora.app/login",
        cta_label="Access Portal",
        footer_text="Sent securely on behalf of the HR Team."
    )
    send_via_integration(employee_email, subject, message, html_body=html_body)

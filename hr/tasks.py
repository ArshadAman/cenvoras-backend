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
                <a href="https://cenvora.co.in/login" class="btn-primary">Access Your Portal</a>
            </div>
            <div class="welcome-text" style="font-size: 14px; margin-top: 20px;">
                <em>Note: For security reasons, please log in and update your password immediately upon access.</em>
            </div>
            <div class="footer">
                &copy; 2026 Cenvora App. All rights reserved. Sent securely on behalf of {business_name} HR Team.
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
                {footer_text or '&copy; 2026 Cenvora App. All rights reserved.'}
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


@shared_task
def send_employee_offer_letter_email(employee_id):
    """
    Sends an executive corporate offer letter email to a newly onboarded employee.
    Branded strictly with the employer's business name (no Cenvora branding, no AI slop).
    """
    import logging
    from decimal import Decimal
    from hr.models import Employee
    from integration.tasks import send_async_email_notification

    logger = logging.getLogger(__name__)

    try:
        emp = Employee.objects.select_related('tenant', 'department', 'designation', 'reporting_manager').get(id=employee_id)
    except Employee.DoesNotExist:
        logger.error("send_employee_offer_letter_email failed: Employee %s does not exist", employee_id)
        return

    if not emp.personal_email:
        logger.info("send_employee_offer_letter_email skipped: Employee %s has no personal email", employee_id)
        return

    tenant = emp.tenant
    business_name = getattr(tenant, 'business_name', '') or "The Management"
    candidate_name = emp.full_name
    designation = emp.designation.name if emp.designation else "Team Member"
    department = emp.department.name if emp.department else "General"
    employment_type_display = emp.get_employment_type_display() if hasattr(emp, 'get_employment_type_display') else "Full-Time"
    doj_str = emp.date_of_joining.strftime("%d %B %Y") if emp.date_of_joining else "Mutually Agreed Date"
    location = emp.work_state or "Company Office"
    manager = emp.reporting_manager.full_name if emp.reporting_manager else "Department Head / Management"

    # Compensation details
    if emp.employment_type == 'contract':
        fee = emp.contract_fee or Decimal('0.00')
        comp_summary_html = f"""
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569; width: 40%;">Compensation Type</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">Professional Contract Fee</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Monthly Contract Fee</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-weight: 700;">₹{fee:,.2f} / month</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Annual Value</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">₹{(fee * 12):,.2f} / annum</td>
        </tr>
        """
    else:
        latest = emp.salary_assignments.order_by('-effective_from', '-id').first()
        ctc = latest.monthly_ctc if latest else Decimal('0.00')
        components = dict(latest.computed_components or {}) if latest else {}
        basic = Decimal(str(components.get('Basic') or components.get('basic') or (ctc * Decimal('0.50')))).quantize(Decimal('0.01'))
        hra = Decimal(str(components.get('HRA') or components.get('hra') or '0.00')).quantize(Decimal('0.01'))
        annual_ctc = (ctc * Decimal('12')).quantize(Decimal('0.01'))
        comp_summary_html = f"""
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569; width: 40%;">Annual CTC</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-weight: 700;">₹{annual_ctc:,.2f} / annum</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Monthly Gross CTC</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-weight: 700;">₹{ctc:,.2f} / month</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Basic Salary</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">₹{basic:,.2f} / month</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">House Rent Allowance (HRA)</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">₹{hra:,.2f} / month</td>
        </tr>
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Statutory Benefits</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">Provident Fund (EPF) &amp; ESI as per statutory regulations</td>
        </tr>
        """

    subject = f"Official Offer of Employment: {designation} at {business_name}"
    plain_text = f"""Dear {candidate_name},

On behalf of {business_name}, we are pleased to offer you the position of {designation}.

Position: {designation}
Department: {department}
Employment Type: {employment_type_display}
Date of Joining: {doj_str}
Work Location: {location}
Reporting Authority: {manager}

Please review the offer details and confirm your acceptance.

Sincerely,
HR Department
{business_name}"""

    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>Offer of Employment</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px 0;">
        <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 640px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); overflow: hidden;">
            <tr>
                <td style="padding: 32px 32px 24px; background-color: #0f172a; color: #ffffff; text-align: left;">
                    <div style="font-size: 22px; font-weight: 800; letter-spacing: -0.02em; color: #ffffff;">{business_name}</div>
                    <div style="font-size: 13px; color: #94a3b8; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">Formal Offer of Employment</div>
                </td>
            </tr>
            <tr>
                <td style="padding: 32px 32px 16px;">
                    <p style="font-size: 16px; line-height: 24px; margin: 0 0 16px; color: #0f172a; font-weight: 600;">Dear {candidate_name},</p>
                    <p style="font-size: 14px; line-height: 22px; margin: 0 0 20px; color: #334155;">
                        On behalf of <strong>{business_name}</strong>, we are delighted to offer you the position of <strong>{designation}</strong>. We were thoroughly impressed by your credentials and look forward to the leadership and value you will bring to our team.
                    </p>
                    <p style="font-size: 14px; line-height: 22px; margin: 0 0 20px; color: #334155;">
                        A summary of your key terms of employment is set out below:
                    </p>

                    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse; margin-bottom: 24px; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; font-size: 13px;">
                        <tr style="background-color: #f1f5f9;">
                            <th colspan="2" style="padding: 10px 14px; text-align: left; font-size: 13px; font-weight: 700; color: #1e293b; border-bottom: 1px solid #cbd5e1;">Employment Specifications</th>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569; width: 40%;">Designation / Role</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-weight: 600;">{designation}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Department</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">{department}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Employment Category</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">{employment_type_display}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Commencement Date</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a; font-weight: 600;">{doj_str}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Work Location</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">{location}</td>
                        </tr>
                        <tr>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #475569;">Reporting Authority</td>
                            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #0f172a;">{manager}</td>
                        </tr>
                        <tr style="background-color: #f1f5f9;">
                            <th colspan="2" style="padding: 10px 14px; text-align: left; font-size: 13px; font-weight: 700; color: #1e293b; border-top: 1px solid #cbd5e1; border-bottom: 1px solid #cbd5e1;">Compensation &amp; Remuneration Terms</th>
                        </tr>
                        {comp_summary_html}
                    </table>

                    <p style="font-size: 14px; line-height: 22px; margin: 0 0 16px; color: #334155;">
                        Please confirm your formal acceptance of this offer by replying directly to this email with your written consent and proposed joining date.
                    </p>
                    <p style="font-size: 14px; line-height: 22px; margin: 0 0 24px; color: #334155;">
                        We look forward to welcoming you aboard and partnering in our mutual growth and success.
                    </p>

                    <div style="margin-top: 28px; padding-top: 16px; border-top: 1px solid #e2e8f0;">
                        <p style="font-size: 14px; line-height: 20px; margin: 0; color: #0f172a; font-weight: 600;">Sincerely,</p>
                        <p style="font-size: 13px; line-height: 18px; margin: 4px 0 0; color: #475569;">Human Resources &amp; Talent Acquisition</p>
                        <p style="font-size: 13px; line-height: 18px; margin: 2px 0 0; color: #0f172a; font-weight: 700;">{business_name}</p>
                    </div>
                </td>
            </tr>
            <tr>
                <td style="padding: 16px 32px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; text-align: center; font-size: 12px; color: #94a3b8;">
                    This communication is strictly confidential and intended solely for {candidate_name}. &copy; {business_name}. All rights reserved.
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    send_async_email_notification.delay(
        str(tenant.id),
        emp.personal_email,
        subject,
        plain_text,
        'Employee',
        str(emp.id),
        html_body=html_body
    )

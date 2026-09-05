import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

def generate_payslip_pdf(payslip):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.setFont("Helvetica-Bold", 16)
    p.drawString(200, height - 50, "Cenvoras ERP - Payslip")

    p.setFont("Helvetica", 12)
    p.drawString(50, height - 100, f"Employee: {payslip.employee.full_name} ({payslip.employee.employee_code})")
    
    designation = payslip.employee.designation.name if payslip.employee.designation else 'N/A'
    p.drawString(50, height - 120, f"Designation: {designation}")
    p.drawString(50, height - 140, f"Month/Year: {payslip.payroll_run.month}/{payslip.payroll_run.year}")

    p.drawString(50, height - 180, f"Present Days: {payslip.present_days} / {payslip.total_working_days}")
    
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, height - 220, "Earnings")
    p.drawString(300, height - 220, "Deductions")
    
    p.setFont("Helvetica", 12)
    y_earn = height - 250
    for name, amt in payslip.earnings.items():
        p.drawString(50, y_earn, f"{name}: {amt}")
        y_earn -= 20
        
    y_ded = height - 250
    for name, amt in payslip.deductions.items():
        p.drawString(300, y_ded, f"{name}: {amt}")
        y_ded -= 20

    y_total = min(y_earn, y_ded) - 40
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y_total, f"Gross Salary: {payslip.gross_salary}")
    p.drawString(300, y_total, f"Net Salary: {payslip.net_salary}")

    p.showPage()
    p.save()
    
    buffer.seek(0)
    return buffer.getvalue()

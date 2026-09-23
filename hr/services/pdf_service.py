import io
import calendar
from decimal import Decimal
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def generate_payslip_pdf(payslip):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    company_style = ParagraphStyle(
        'CompanyHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#1E1B4B'),
        alignment=1,  # Center
    )
    subtitle_style = ParagraphStyle(
        'PayslipSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#4B5563'),
        alignment=1,
    )
    section_title = ParagraphStyle(
        'SectionTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#1E293B'),
    )
    cell_label = ParagraphStyle(
        'CellLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#475569'),
    )
    cell_val = ParagraphStyle(
        'CellValue',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0F172A'),
    )
    tbl_header = ParagraphStyle(
        'TblHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.white,
    )
    tbl_cell = ParagraphStyle(
        'TblCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1E293B'),
    )
    tbl_cell_bold = ParagraphStyle(
        'TblCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0F172A'),
    )
    reason_subtext = ParagraphStyle(
        'ReasonSubtext',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor('#64748B'),
    )

    story = []

    # 1. Header
    company_name = getattr(payslip.tenant, 'business_name', None) or "Cenvoras Enterprise"
    month_name = calendar.month_name[payslip.payroll_run.month]
    year = payslip.payroll_run.year

    story.append(Paragraph(company_name, company_style))
    story.append(Paragraph(f"Payslip for the month of {month_name} {year}", subtitle_style))
    story.append(Spacer(1, 14))

    # 2. Employee Summary Info Table (2 columns of key-values)
    emp = payslip.employee
    designation = emp.designation.name if emp.designation else 'N/A'
    dept = emp.department.name if emp.department else 'N/A'
    doj = str(emp.date_of_joining) if emp.date_of_joining else 'N/A'
    pan = emp.pan_number or 'N/A'
    uan = emp.uan or 'N/A'
    bank_acc = emp.bank_account_number or 'N/A'
    bank_ifsc = emp.bank_ifsc or 'N/A'

    info_data = [
        [
            Paragraph("Employee Name:", cell_label),
            Paragraph(f"{emp.full_name} ({emp.employee_code})", cell_val),
            Paragraph("Designation:", cell_label),
            Paragraph(designation, cell_val),
        ],
        [
            Paragraph("Department:", cell_label),
            Paragraph(dept, cell_val),
            Paragraph("Date of Joining:", cell_label),
            Paragraph(doj, cell_val),
        ],
        [
            Paragraph("PAN Number:", cell_label),
            Paragraph(pan, cell_val),
            Paragraph("UAN:", cell_label),
            Paragraph(uan, cell_val),
        ],
        [
            Paragraph("Bank Account:", cell_label),
            Paragraph(bank_acc, cell_val),
            Paragraph("Bank IFSC:", cell_label),
            Paragraph(bank_ifsc, cell_val),
        ],
        [
            Paragraph("Total Working Days:", cell_label),
            Paragraph(str(payslip.total_working_days), cell_val),
            Paragraph("Days Paid / Present:", cell_label),
            Paragraph(f"{payslip.present_days} (LOP: {payslip.lop_days})", cell_val),
        ],
    ]

    info_table = Table(info_data, colWidths=[110, 150, 110, 150])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # 3. Earnings & Deductions Tables
    earnings_rows = [[Paragraph("Earnings Component", tbl_header), Paragraph("Amount (Rs.)", tbl_header)]]
    earnings_items = dict(payslip.earnings or {})
    if not earnings_items and float(payslip.gross_salary or 0) > 0:
        earnings_items["Base Earnings"] = payslip.gross_salary
    if float(payslip.overtime_amount or 0) > 0 and "Overtime" not in earnings_items:
        earnings_items["Overtime"] = payslip.overtime_amount

    for cname, amt in earnings_items.items():
        clean_name = str(cname).replace('₹', 'Rs. ')
        earnings_rows.append([
            Paragraph(clean_name, tbl_cell),
            Paragraph(f"Rs. {float(amt):,.2f}", tbl_cell),
        ])
    earnings_rows.append([
        Paragraph("Gross Salary", tbl_cell_bold),
        Paragraph(f"Rs. {float(payslip.gross_salary):,.2f}", tbl_cell_bold),
    ])

    deductions_rows = [[Paragraph("Deductions Component", tbl_header), Paragraph("Amount (Rs.)", tbl_header)]]
    raw_deductions = dict(payslip.deductions or {})
    reasons_map = dict(payslip.deduction_reasons or {})

    # Ensure statutory items are displayed even if not pre-populated in deductions json
    if float(payslip.employee_pf or 0) > 0 and 'PF' not in raw_deductions and 'Provident Fund (PF)' not in raw_deductions:
        raw_deductions['Provident Fund (PF)'] = payslip.employee_pf
    if float(payslip.employee_esi or 0) > 0 and 'ESI' not in raw_deductions:
        raw_deductions['State Insurance (ESI)'] = payslip.employee_esi
    if float(payslip.tds or 0) > 0 and 'TDS' not in raw_deductions and 'Income Tax (TDS)' not in raw_deductions:
        raw_deductions['Income Tax (TDS)'] = payslip.tds
    if float(payslip.professional_tax or 0) > 0 and 'PT' not in raw_deductions and 'Professional Tax (PT)' not in raw_deductions:
        raw_deductions['Professional Tax (PT)'] = payslip.professional_tax
    if float(payslip.advance_recovery or 0) > 0 and 'Salary Advance Recovery' not in raw_deductions:
        raw_deductions['Salary Advance Recovery'] = payslip.advance_recovery
    if float(payslip.loan_recovery or 0) > 0 and 'Loan Recovery' not in raw_deductions:
        raw_deductions['Loan Recovery'] = payslip.loan_recovery
    if float(payslip.lop_days or 0) > 0 and 'Loss of Pay (LOP)' not in raw_deductions:
        working_days = Decimal(str(payslip.total_working_days or 26))
        present_days = Decimal(str(payslip.present_days or 0))
        daily_rate = Decimal(str(payslip.gross_salary)) / (present_days if present_days > 0 else working_days)
        lop_amt = (Decimal(str(payslip.lop_days)) * daily_rate).quantize(Decimal('0.01'))
        if lop_amt > 0:
            raw_deductions['Loss of Pay (LOP)'] = str(lop_amt)
    if not raw_deductions and float(payslip.total_deductions or 0) > 0:
        raw_deductions['Statutory & Other Deductions'] = str(payslip.total_deductions)

    for cname, amt in raw_deductions.items():
        reason_info = reasons_map.get(cname, {})
        reason_text = reason_info.get('reason', '') if isinstance(reason_info, dict) else str(reason_info or '')
        if not reason_text:
            if 'PF' in cname or 'Provident' in cname:
                reason_text = "Employee statutory 12.00% Provident Fund contribution on Basic salary"
            elif 'ESI' in cname:
                reason_text = "Employee statutory 0.75% State Insurance contribution on gross earnings"
            elif 'TDS' in cname or 'Tax' in cname:
                reason_text = "Monthly income tax withholding deducted under Section 192"
            elif 'PT' in cname or 'Professional' in cname:
                reason_text = "State statutory Professional Tax slab deduction"
            elif 'Advance' in cname:
                reason_text = "Monthly installment recovered against active salary advance"
            elif 'Loan' in cname:
                reason_text = "Monthly personal loan recovery installment"
            elif 'Loss of Pay' in cname or 'LOP' in cname:
                reason_text = f"{payslip.lop_days} day(s) unpaid leave / absence docked from monthly salary"

        clean_cname = str(cname).replace('₹', 'Rs. ')
        clean_reason = str(reason_text).replace('₹', 'Rs. ').replace('≤', '<=').replace('≥', '>=')
        
        comp_cell_content = [Paragraph(clean_cname, tbl_cell)]
        if clean_reason:
            comp_cell_content.append(Paragraph(clean_reason, reason_subtext))
            
        deductions_rows.append([
            comp_cell_content,
            Paragraph(f"Rs. {float(amt):,.2f}", tbl_cell),
        ])
    deductions_rows.append([
        Paragraph("Total Deductions", tbl_cell_bold),
        Paragraph(f"Rs. {float(payslip.total_deductions):,.2f}", tbl_cell_bold),
    ])

    earn_table = Table(earnings_rows, colWidths=[160, 95])
    earn_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#312E81')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#EEF2FF')),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    ded_table = Table(deductions_rows, colWidths=[175, 90])
    ded_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7F1D1D')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FEF2F2')),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    side_by_side = Table([
        [earn_table, ded_table]
    ], colWidths=[260, 260])
    side_by_side.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(side_by_side)
    story.append(Spacer(1, 14))

    # 4. Net Take-Home Highlight Banner
    net_data = [
        [
            Paragraph("NET TAKE-HOME SALARY:", ParagraphStyle('NetLabel', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.HexColor('#065F46'))),
            Paragraph(f"Rs. {float(payslip.net_salary):,.2f}", ParagraphStyle('NetValue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#065F46'), alignment=2)),
        ]
    ]
    net_table = Table(net_data, colWidths=[260, 260])
    net_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ECFDF5')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#10B981')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(net_table)
    story.append(Spacer(1, 12))

    # 5. Employer Contribution Summary (Compliance Transparency)
    empr_data = [
        [
            Paragraph(f"Employer PF: Rs. {float(payslip.employer_pf):,.2f} (EPF: Rs. {float(payslip.employer_epf):,.2f}, EPS: Rs. {float(payslip.employer_eps):,.2f})", cell_val),
            Paragraph(f"Employer ESI: Rs. {float(payslip.employer_esi):,.2f}", cell_val),
            Paragraph(f"Total Employer Contribution: Rs. {float(payslip.employer_total_contribution):,.2f}", cell_label),
        ]
    ]
    empr_table = Table(empr_data, colWidths=[240, 130, 150])
    empr_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F1F5F9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(empr_table)
    story.append(Spacer(1, 16))

    # Footer note
    footer_text = ParagraphStyle('Footer', parent=styles['Normal'], fontName='Helvetica-Oblique', fontSize=8, leading=10, textColor=colors.HexColor('#94A3B8'), alignment=1)
    story.append(Paragraph("This is a system-generated payslip from Cenvoras ERP and does not require a physical signature.", footer_text))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

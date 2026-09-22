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
    earnings_rows = [[Paragraph("Earnings Component", tbl_header), Paragraph("Amount (₹)", tbl_header)]]
    for cname, amt in payslip.earnings.items():
        earnings_rows.append([
            Paragraph(cname, tbl_cell),
            Paragraph(f"₹{float(amt):,.2f}", tbl_cell),
        ])
    earnings_rows.append([
        Paragraph("Gross Salary", tbl_cell_bold),
        Paragraph(f"₹{float(payslip.gross_salary):,.2f}", tbl_cell_bold),
    ])

    deductions_rows = [[Paragraph("Deductions Component", tbl_header), Paragraph("Amount (₹)", tbl_header)]]
    reasons_map = payslip.deduction_reasons or {}

    for cname, amt in payslip.deductions.items():
        reason_info = reasons_map.get(cname, {})
        reason_text = reason_info.get('reason', '') if isinstance(reason_info, dict) else str(reason_info)
        
        comp_cell_content = [Paragraph(cname, tbl_cell)]
        if reason_text:
            comp_cell_content.append(Paragraph(reason_text, reason_subtext))
            
        deductions_rows.append([
            comp_cell_content,
            Paragraph(f"₹{float(amt):,.2f}", tbl_cell),
        ])
    deductions_rows.append([
        Paragraph("Total Deductions", tbl_cell_bold),
        Paragraph(f"₹{float(payslip.total_deductions):,.2f}", tbl_cell_bold),
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
            Paragraph(f"₹{float(payslip.net_salary):,.2f}", ParagraphStyle('NetValue', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#065F46'), alignment=2)),
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
            Paragraph(f"Employer PF: ₹{float(payslip.employer_pf):,.2f} (EPF: ₹{float(payslip.employer_epf):,.2f}, EPS: ₹{float(payslip.employer_eps):,.2f})", cell_val),
            Paragraph(f"Employer ESI: ₹{float(payslip.employer_esi):,.2f}", cell_val),
            Paragraph(f"Total Employer Contribution: ₹{float(payslip.employer_total_contribution):,.2f}", cell_label),
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

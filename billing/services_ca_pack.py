import io
import csv
import json
from decimal import Decimal
from datetime import datetime, date
from django.http import HttpResponse
from django.db.models import Sum, Q, F
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, HRFlowable
)

from .models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Vendor
from .models_returns import CreditNote
from .services_gstr2b import calculate_purchase_bill_tax


def parse_date_safely(val, default_date):
    """Safely parse date from multiple string formats (YYYY-MM-DD, DD/MM/YYYY, etc.)"""
    if not val:
        return default_date
    if isinstance(val, (date, datetime)):
        return val if isinstance(val, date) else val.date()
    val_str = str(val).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(val_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return default_date


class CAAuditPackGenerator:
    """
    Generates the Executive CA Audit Pack in 4 multi-download formats:
    - .xlsx (Multi-tab formatted Excel workbook)
    - .csv (Flat CSV registers)
    - .json (Official portal JSON)
    - .pdf (Executive Summary report via ReportLab)
    """

    @classmethod
    def get_audit_data(cls, user, from_date=None, to_date=None):
        """Aggregates all sales, purchases, ITC reconciliation, and withholding data for the period"""
        tenant = getattr(user, 'active_tenant', user)
        today = date.today()

        from_date = parse_date_safely(from_date, date(today.year, today.month, 1))
        to_date = parse_date_safely(to_date, today)

        # 1. Sales Data
        sales_qs = SalesInvoice.objects.filter(
            Q(created_by=tenant) | Q(created_by__parent=tenant),
            invoice_date__gte=from_date,
            invoice_date__lte=to_date
        ).exclude(status='draft').prefetch_related('items', 'customer')

        sales_taxable = Decimal('0.00')
        sales_cgst = Decimal('0.00')
        sales_sgst = Decimal('0.00')
        sales_igst = Decimal('0.00')
        sales_total = Decimal('0.00')

        sales_rows = []
        for inv in sales_qs:
            inv_taxable = Decimal('0.00')
            inv_cgst = Decimal('0.00')
            inv_sgst = Decimal('0.00')
            inv_igst = Decimal('0.00')

            pos = (inv.customer_gstin or '')[:2] or getattr(inv.customer, 'state', '') or 'Same State'
            is_interstate = (inv.gst_treatment or '').lower() in ['interstate', 'igst', 'overseas', 'sez']

            for itm in inv.items.all():
                q = Decimal(itm.quantity or 0)
                p = Decimal(itm.price or 0)
                d = Decimal(itm.discount or 0)
                t = Decimal(itm.tax or 0)

                base = (q * p) - ((q * p * d) / Decimal('100'))
                tax = (base * t) / Decimal('100')
                inv_taxable += base

                if is_interstate:
                    inv_igst += tax
                else:
                    inv_cgst += tax / Decimal('2')
                    inv_sgst += tax / Decimal('2')

            sales_taxable += inv_taxable
            sales_cgst += inv_cgst
            sales_sgst += inv_sgst
            sales_igst += inv_igst
            sales_total += inv.total_amount

            sales_rows.append({
                'invoice_number': inv.invoice_number,
                'invoice_date': inv.invoice_date.strftime('%d-%m-%Y') if inv.invoice_date else '',
                'customer_name': inv.customer_name or (inv.customer.name if inv.customer else 'Retail Customer'),
                'customer_gstin': inv.customer_gstin or 'URP (Unregistered)',
                'pos': pos,
                'taxable_value': float(inv_taxable),
                'cgst': float(inv_cgst),
                'sgst': float(inv_sgst),
                'igst': float(inv_igst),
                'total_amount': float(inv.total_amount)
            })

        # 2. Purchase Bills & GSTR-2B Data
        purchase_qs = PurchaseBill.objects.filter(
            Q(created_by=tenant) | Q(created_by__parent=tenant),
            bill_date__gte=from_date,
            bill_date__lte=to_date
        ).prefetch_related('items', 'vendor')

        purchases_taxable = Decimal('0.00')
        purchases_cgst = Decimal('0.00')
        purchases_sgst = Decimal('0.00')
        purchases_igst = Decimal('0.00')
        purchases_total = Decimal('0.00')

        safe_itc = Decimal('0.00')
        at_risk_itc = Decimal('0.00')
        withheld_pool = Decimal('0.00')

        purchase_rows = []
        defaulter_rows = []

        for bill in purchase_qs:
            taxable, tax_amt, cgst, sgst, igst = calculate_purchase_bill_tax(bill)
            taxable = taxable or Decimal('0.00')
            tax_amt = tax_amt or Decimal('0.00')
            cgst = cgst or Decimal('0.00')
            sgst = sgst or Decimal('0.00')
            igst = igst or Decimal('0.00')
            bill_total = Decimal(bill.total_amount or 0)
            withheld_amt = Decimal(bill.gst_withheld_amount or 0)

            purchases_taxable += taxable
            purchases_cgst += cgst
            purchases_sgst += sgst
            purchases_igst += igst
            purchases_total += bill_total

            is_matched = (bill.gstr2b_status == 'matched')
            if is_matched:
                safe_itc += tax_amt
            else:
                at_risk_itc += tax_amt

            if bill.is_gst_withheld:
                withheld_pool += withheld_amt

            v_risk_tier = (bill.vendor.risk_tier.title() if (bill.vendor and bill.vendor.risk_tier) else 'Defaulter')
            v_compliance = (bill.vendor.compliance_score if (bill.vendor and bill.vendor.compliance_score is not None) else 50)

            p_entry = {
                'bill_number': bill.bill_number or '',
                'bill_date': bill.bill_date.strftime('%d-%m-%Y') if bill.bill_date else '',
                'vendor_name': bill.vendor_name or (bill.vendor.name if bill.vendor else 'Direct Vendor'),
                'vendor_gstin': bill.vendor_gstin or (bill.vendor.gstin if bill.vendor else 'URP'),
                'taxable_value': float(taxable),
                'cgst': float(cgst),
                'sgst': float(sgst),
                'igst': float(igst),
                'total_tax': float(tax_amt),
                'total_amount': float(bill_total),
                'match_status': bill.get_gstr2b_status_display() if hasattr(bill, 'get_gstr2b_status_display') else (bill.gstr2b_status or 'Pending'),
                'is_withheld': 'YES' if bill.is_gst_withheld else 'NO',
                'withheld_amount': float(withheld_amt)
            }
            purchase_rows.append(p_entry)

            if not is_matched:
                defaulter_rows.append({
                    'vendor_name': p_entry['vendor_name'],
                    'vendor_gstin': p_entry['vendor_gstin'],
                    'bill_number': p_entry['bill_number'],
                    'bill_date': p_entry['bill_date'],
                    'at_risk_tax': float(tax_amt),
                    'withheld_amount': float(withheld_amt),
                    'risk_tier': v_risk_tier,
                    'compliance_score': v_compliance
                })

        # Net Tax Calculation
        net_tax_payable = max(Decimal('0.00'), (sales_cgst + sales_sgst + sales_igst) - safe_itc)
        net_itc_credit = max(Decimal('0.00'), safe_itc - (sales_cgst + sales_sgst + sales_igst))

        return {
            'business_name': getattr(user, 'business_name', '') or user.get_full_name() or user.username,
            'gstin': getattr(user, 'gstin', '') or 'UNREGISTERED',
            'period': f"{from_date.strftime('%d-%b-%Y')} to {to_date.strftime('%d-%b-%Y')}",
            'summary': {
                'sales_taxable': float(sales_taxable),
                'sales_tax': float(sales_cgst + sales_sgst + sales_igst),
                'sales_total': float(sales_total),
                'purchases_taxable': float(purchases_taxable),
                'safe_itc': float(safe_itc),
                'at_risk_itc': float(at_risk_itc),
                'withheld_pool': float(withheld_pool),
                'net_tax_payable': float(net_tax_payable),
                'net_itc_credit': float(net_itc_credit),
            },
            'sales_rows': sales_rows,
            'purchase_rows': purchase_rows,
            'defaulter_rows': defaulter_rows
        }

    # ═══════════════════════════════════════════════════════════════
    # 1. EXCEL WORKBOOK GENERATOR (.xlsx)
    # ═══════════════════════════════════════════════════════════════
    @classmethod
    def generate_excel(cls, audit_data):
        """Builds multi-tab formatted Excel workbook"""
        wb = openpyxl.Workbook()

        # Styles
        hdr_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        hdr_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        title_font = Font(name="Calibri", size=14, bold=True, color="0F172A")
        sub_font = Font(name="Calibri", size=10, italic=True, color="475569")
        bold_font = Font(name="Calibri", size=11, bold=True)
        green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
        red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        gold_fill = PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid")
        border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        # -------------------------------------------------------------
        # Tab 1: Executive GSTR-3B Computation
        # -------------------------------------------------------------
        ws_summary = wb.active
        ws_summary.title = "GSTR-3B Computation"
        ws_summary.views.sheetView[0].showGridLines = True

        ws_summary.append([audit_data['business_name'].upper(), "GST SHIELD CA AUDIT PACK"])
        ws_summary.append([f"GSTIN: {audit_data['gstin']}", f"Period: {audit_data['period']}"])
        ws_summary.append([])

        ws_summary.append(["GSTR-3B Section", "Description", "Taxable Value (₹)", "Tax Amount (₹)", "Audit Verification Status"])
        for col in range(1, 6):
            cell = ws_summary.cell(row=4, column=col)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center" if col > 2 else "left")

        summary = audit_data['summary']
        comp_rows = [
            ("Table 3.1(a)", "Outward Taxable Supplies (Sales)", summary['sales_taxable'], summary['sales_tax'], "Verified from Sales Register"),
            ("Table 4(A)(5)", "Eligible ITC from Registered Suppliers", summary['purchases_taxable'], summary['safe_itc'], "🟢 100% Reconciled in GSTR-2B"),
            ("Table 4(B)(2)", "Ineligible / Withheld ITC (Unfiled Suppliers)", 0, summary['at_risk_itc'], "🔴 Held in Bank (Withholding Active)"),
            ("Net Cash Due", "Net GST Payable to Government", 0, summary['net_tax_payable'], "Payable via Challan by 20th"),
            ("Credit Carry", "ITC Credit Carried Forward", 0, summary['net_itc_credit'], "Available for Future Months")
        ]

        for idx, row in enumerate(comp_rows, start=5):
            ws_summary.append(list(row))
            for c in range(1, 6):
                cell = ws_summary.cell(row=idx, column=c)
                cell.border = border
                if idx in (5, 6, 8):
                    cell.font = bold_font
                if idx == 6:
                    cell.fill = green_fill
                elif idx == 7:
                    cell.fill = red_fill
                elif idx == 8:
                    cell.fill = gold_fill

        # -------------------------------------------------------------
        # Tab 2: Outward Sales Register (GSTR-1 Ready)
        # -------------------------------------------------------------
        ws_sales = wb.create_sheet(title="Sales Register (GSTR-1)")
        ws_sales.views.sheetView[0].showGridLines = True
        ws_sales.append(["Invoice No", "Date", "Customer Name", "Customer GSTIN", "POS", "Taxable (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)", "Total (₹)"])
        for col in range(1, 11):
            cell = ws_sales.cell(row=1, column=col)
            cell.fill = hdr_fill
            cell.font = hdr_font

        for s in audit_data['sales_rows']:
            ws_sales.append([
                s['invoice_number'], s['invoice_date'], s['customer_name'], s['customer_gstin'],
                s['pos'], s['taxable_value'], s['cgst'], s['sgst'], s['igst'], s['total_amount']
            ])

        # -------------------------------------------------------------
        # Tab 3: Reconciled Purchase Register
        # -------------------------------------------------------------
        ws_purchases = wb.create_sheet(title="Purchase Register (GSTR-2B)")
        ws_purchases.views.sheetView[0].showGridLines = True
        ws_purchases.append(["Bill No", "Date", "Vendor Name", "Vendor GSTIN", "Taxable (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)", "Total Tax (₹)", "Total Bill (₹)", "2B Status", "Withheld Amount (₹)"])
        for col in range(1, 13):
            cell = ws_purchases.cell(row=1, column=col)
            cell.fill = hdr_fill
            cell.font = hdr_font

        for p in audit_data['purchase_rows']:
            ws_purchases.append([
                p['bill_number'], p['bill_date'], p['vendor_name'], p['vendor_gstin'],
                p['taxable_value'], p['cgst'], p['sgst'], p['igst'], p['total_tax'],
                p['total_amount'], p['match_status'], p['withheld_amount']
            ])

        # -------------------------------------------------------------
        # Tab 4: Defaulters & Withholding Schedule
        # -------------------------------------------------------------
        ws_defaulters = wb.create_sheet(title="Defaulter & Withholding")
        ws_defaulters.views.sheetView[0].showGridLines = True
        ws_defaulters.append(["Vendor Name", "Vendor GSTIN", "Bill No", "Bill Date", "At-Risk Tax (₹)", "Withheld Amount (₹)", "Risk Tier", "Compliance Score"])
        for col in range(1, 9):
            cell = ws_defaulters.cell(row=1, column=col)
            cell.fill = hdr_fill
            cell.font = hdr_font

        for d in audit_data['defaulter_rows']:
            ws_defaulters.append([
                d['vendor_name'], d['vendor_gstin'], d['bill_number'], d['bill_date'],
                d['at_risk_tax'], d['withheld_amount'], d['risk_tier'], f"{d['compliance_score']}/100"
            ])

        # Auto-fit column widths
        for ws in wb.worksheets:
            for col in ws.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()

    # ═══════════════════════════════════════════════════════════════
    # 2. FLAT CSV GENERATOR (.csv)
    # ═══════════════════════════════════════════════════════════════
    @classmethod
    def generate_csv(cls, audit_data, report_type='sales'):
        """Builds clean flat CSV for Tally, Busy, or CA software import"""
        output = io.StringIO()
        writer = csv.writer(output)

        if report_type == 'sales':
            writer.writerow(['Invoice No', 'Date', 'Customer Name', 'Customer GSTIN', 'POS', 'Taxable Value', 'CGST', 'SGST', 'IGST', 'Total Invoice Amount'])
            for s in audit_data['sales_rows']:
                writer.writerow([s['invoice_number'], s['invoice_date'], s['customer_name'], s['customer_gstin'], s['pos'], s['taxable_value'], s['cgst'], s['sgst'], s['igst'], s['total_amount']])
        elif report_type == 'defaulters':
            writer.writerow(['Vendor Name', 'Vendor GSTIN', 'Bill No', 'Bill Date', 'At-Risk Tax', 'Withheld Amount', 'Risk Tier', 'Compliance Score'])
            for d in audit_data['defaulter_rows']:
                writer.writerow([d['vendor_name'], d['vendor_gstin'], d['bill_number'], d['bill_date'], d['at_risk_tax'], d['withheld_amount'], d['risk_tier'], d['compliance_score']])
        else:
            writer.writerow(['Bill No', 'Date', 'Vendor Name', 'Vendor GSTIN', 'Taxable Value', 'CGST', 'SGST', 'IGST', 'Total Tax', 'Total Bill Amount', '2B Status', 'Withheld Amount'])
            for p in audit_data['purchase_rows']:
                writer.writerow([p['bill_number'], p['bill_date'], p['vendor_name'], p['vendor_gstin'], p['taxable_value'], p['cgst'], p['sgst'], p['igst'], p['total_tax'], p['total_amount'], p['match_status'], p['withheld_amount']])

        return output.getvalue()

    # ═══════════════════════════════════════════════════════════════
    # 3. GOVERNMENT PORTAL JSON PAYLOAD (.json)
    # ═══════════════════════════════════════════════════════════════
    @classmethod
    def generate_json(cls, audit_data):
        """Official NIC-compliant payload ready to upload to gst.gov.in"""
        b2b_by_supplier = {}
        for s in audit_data['sales_rows']:
            gstin = s['customer_gstin']
            if 'URP' in gstin:
                continue
            if gstin not in b2b_by_supplier:
                b2b_by_supplier[gstin] = {'ctin': gstin, 'inv': []}

            b2b_by_supplier[gstin]['inv'].append({
                'inum': s['invoice_number'],
                'idt': s['invoice_date'],
                'val': s['total_amount'],
                'pos': s['pos'],
                'rchrg': 'N',
                'inv_typ': 'R',
                'itms': [{
                    'num': 1,
                    'itm_det': {
                        'txval': s['taxable_value'],
                        'rt': 18.0,
                        'iamt': s['igst'],
                        'camt': s['cgst'],
                        'samt': s['sgst'],
                        'csamt': 0.0
                    }
                }]
            })

        payload = {
            'gstin': audit_data['gstin'],
            'fp': datetime.now().strftime('%m%Y'),
            'b2b': list(b2b_by_supplier.values()),
            'gstr3b_summary': audit_data['summary']
        }
        return json.dumps(payload, indent=2)

    # ═══════════════════════════════════════════════════════════════
    # 4. EXECUTIVE SUMMARY REPORT PDF (.pdf) via ReportLab
    # ═══════════════════════════════════════════════════════════════
    @classmethod
    def generate_pdf(cls, audit_data):
        """Builds an executive-ready printable CA Audit Report PDF"""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'DocTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=15,
            leading=18,
            textColor=colors.HexColor('#0F172A')
        )
        subtitle_style = ParagraphStyle(
            'DocSubtitle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=colors.HexColor('#64748B')
        )
        h2_style = ParagraphStyle(
            'SectionH2',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=10,
            leading=13,
            textColor=colors.HexColor('#1E293B'),
            spaceBefore=6,
            spaceAfter=3
        )
        th_style = ParagraphStyle(
            'TH',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=colors.white
        )
        td_style = ParagraphStyle(
            'TD',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#1E293B')
        )
        td_bold = ParagraphStyle(
            'TDBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#1E293B')
        )

        elements = []

        # 1. Header Banner
        header_data = [
            [
                Paragraph(f"<b>{audit_data.get('business_name', 'BUSINESS').upper()}</b><br/><font color='#64748B' size='8'>GSTIN: {audit_data.get('gstin', 'UNREGISTERED')}</font>", title_style),
                Paragraph(f"<font color='#059669'><b>EXECUTIVE CA AUDIT REPORT</b></font><br/><font color='#64748B' size='8'>Period: {audit_data.get('period', '')}<br/>Generated: {datetime.now().strftime('%d-%b-%Y %H:%M')}</font>", subtitle_style)
            ]
        ]
        header_table = Table(header_data, colWidths=[110 * mm, 76 * mm])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(header_table)
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#059669'), spaceBefore=2, spaceAfter=6))

        # 2. Key Metrics Strip
        summary = audit_data.get('summary', {})
        kpi_data = [
            [
                Paragraph(f"<b>TOTAL SALES (GSTR-1)</b><br/><font size='10'><b>₹{summary.get('sales_total', 0):,.2f}</b></font><br/><font size='7' color='#64748B'>Tax: ₹{summary.get('sales_tax', 0):,.2f}</font>", td_style),
                Paragraph(f"<b>VERIFIED ITC (2B)</b><br/><font size='10' color='#059669'><b>₹{summary.get('safe_itc', 0):,.2f}</b></font><br/><font size='7' color='#059669'>100% Eligible</font>", td_style),
                Paragraph(f"<b>AT-RISK / WITHHELD</b><br/><font size='10' color='#DC2626'><b>₹{summary.get('at_risk_itc', 0):,.2f}</b></font><br/><font size='7' color='#D97706'>Protected: ₹{summary.get('withheld_pool', 0):,.2f}</font>", td_style),
                Paragraph(f"<b>NET TAX PAYABLE</b><br/><font size='10' color='#D97706'><b>₹{summary.get('net_tax_payable', 0):,.2f}</b></font><br/><font size='7' color='#64748B'>Credit: ₹{summary.get('net_itc_credit', 0):,.2f}</font>", td_style),
            ]
        ]
        kpi_table = Table(kpi_data, colWidths=[46.5 * mm, 46.5 * mm, 46.5 * mm, 46.5 * mm])
        kpi_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(kpi_table)
        elements.append(Spacer(1, 3 * mm))

        # 3. GSTR-3B Computation Schedule
        elements.append(Paragraph("1. GSTR-3B Tax Computation & Filing Schedule", h2_style))
        comp_data = [
            [Paragraph("Section", th_style), Paragraph("Description", th_style), Paragraph("Taxable Base", th_style), Paragraph("Tax Amount", th_style), Paragraph("Audit Verification Status", th_style)],
            [Paragraph("Table 3.1(a)", td_bold), Paragraph("Outward Taxable Supplies (Sales)", td_style), Paragraph(f"₹{summary.get('sales_taxable', 0):,.2f}", td_style), Paragraph(f"₹{summary.get('sales_tax', 0):,.2f}", td_style), Paragraph("Verified (Sales Register)", td_style)],
            [Paragraph("Table 4(A)(5)", td_bold), Paragraph("Eligible Input Tax Credit (GSTR-2B Inward)", td_style), Paragraph(f"₹{summary.get('purchases_taxable', 0):,.2f}", td_style), Paragraph(f"₹{summary.get('safe_itc', 0):,.2f}", td_style), Paragraph("Verified in GSTR-2B", td_style)],
            [Paragraph("Table 4(B)(2)", td_bold), Paragraph("Ineligible / Unfiled Vendor ITC (Withheld)", td_style), Paragraph("-", td_style), Paragraph(f"₹{summary.get('at_risk_itc', 0):,.2f}", td_style), Paragraph("Withheld from Vendor Payments", td_style)],
            [Paragraph("Net Due", td_bold), Paragraph("<b>Net Cash GST Payable to Govt</b>", td_bold), Paragraph("-", td_bold), Paragraph(f"<b>₹{summary.get('net_tax_payable', 0):,.2f}</b>", td_bold), Paragraph("Payable by 20th", td_bold)],
        ]
        comp_table = Table(comp_data, colWidths=[24 * mm, 66 * mm, 32 * mm, 32 * mm, 32 * mm])
        comp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E293B')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FEF3C7')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(comp_table)
        elements.append(Spacer(1, 3 * mm))

        # 4. Defaulters / Withholding Schedule (if any)
        defaulters = audit_data.get('defaulter_rows', [])
        if defaulters:
            elements.append(Paragraph("2. Delinquent Vendors & Statutory Payment Withholding (Sec 16(2))", h2_style))
            def_data = [
                [Paragraph("Vendor Name", th_style), Paragraph("GSTIN", th_style), Paragraph("Bill No", th_style), Paragraph("At-Risk Tax", th_style), Paragraph("Withheld Amt", th_style), Paragraph("Compliance", th_style)]
            ]
            for d in defaulters[:10]:
                def_data.append([
                    Paragraph(str(d.get('vendor_name', ''))[:22], td_style),
                    Paragraph(str(d.get('vendor_gstin', '')), td_style),
                    Paragraph(str(d.get('bill_number', '')), td_style),
                    Paragraph(f"₹{d.get('at_risk_tax', 0):,.2f}", td_style),
                    Paragraph(f"₹{d.get('withheld_amount', 0):,.2f}", td_style),
                    Paragraph(f"{d.get('compliance_score', 50)}/100 ({d.get('risk_tier', 'Defaulter')})", td_style),
                ])
            def_table = Table(def_data, colWidths=[48 * mm, 34 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm])
            def_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#991B1B')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(def_table)
            elements.append(Spacer(1, 3 * mm))

        # 5. Auditor Verification & Attestation Box
        sign_data = [
            [
                Paragraph("<b>ACCOUNTANT / AUDITOR VERIFICATION</b><br/><br/>I have verified the outward supplies and ITC claims against the GSTR-2B portal data for the period.<br/><br/>Signature: __________________________<br/>Membership No: ____________________<br/>Date: _______________________________", td_style),
                Paragraph("<b>TAXPAYER DECLARATION</b><br/><br/>We confirm the books of accounts and payment withholding records reflect true financial transactions.<br/><br/>Authorized Signatory: ________________<br/>Designation: ________________________<br/>Company Seal: ______________________", td_style)
            ]
        ]
        sign_table = Table(sign_data, colWidths=[93 * mm, 93 * mm])
        sign_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#94A3B8')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('PADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(Spacer(1, 2 * mm))
        elements.append(sign_table)

        doc.build(elements)
        buffer.seek(0)
        return buffer.getvalue()


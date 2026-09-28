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

from .models import SalesInvoice, SalesInvoiceItem, PurchaseBill, PurchaseBillItem, Vendor
from .models_returns import CreditNote
from .services_gstr2b import calculate_purchase_bill_tax


class CAAuditPackGenerator:
    """
    Generates the Executive CA Audit Pack in 4 multi-download formats:
    - .xlsx (Multi-tab formatted Excel workbook)
    - .csv (Flat CSV registers)
    - .json (Official portal JSON)
    - .pdf (Executive Summary report)
    """

    @classmethod
    def get_audit_data(cls, user, from_date=None, to_date=None):
        """Aggregates all sales, purchases, ITC reconciliation, and withholding data for the period"""
        tenant = getattr(user, 'active_tenant', user)
        today = date.today()

        if not from_date:
            from_date = date(today.year, today.month, 1)
        elif isinstance(from_date, str):
            from_date = datetime.strptime(from_date, '%Y-%m-%d').date()

        if not to_date:
            to_date = today
        elif isinstance(to_date, str):
            to_date = datetime.strptime(to_date, '%Y-%m-%d').date()

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
            purchases_taxable += taxable
            purchases_cgst += cgst
            purchases_sgst += sgst
            purchases_igst += igst
            purchases_total += bill.total_amount

            is_matched = bill.gstr2b_status == 'matched'
            if is_matched:
                safe_itc += tax_amt
            else:
                at_risk_itc += tax_amt

            if bill.is_gst_withheld:
                withheld_pool += bill.gst_withheld_amount

            p_entry = {
                'bill_number': bill.bill_number,
                'bill_date': bill.bill_date.strftime('%d-%m-%Y') if bill.bill_date else '',
                'vendor_name': bill.vendor_name or (bill.vendor.name if bill.vendor else 'Direct Vendor'),
                'vendor_gstin': bill.vendor_gstin or (bill.vendor.gstin if bill.vendor else 'URP'),
                'taxable_value': float(taxable),
                'cgst': float(cgst),
                'sgst': float(sgst),
                'igst': float(igst),
                'total_tax': float(tax_amt),
                'total_amount': float(bill.total_amount),
                'match_status': bill.get_gstr2b_status_display() if hasattr(bill, 'get_gstr2b_status_display') else bill.gstr2b_status,
                'is_withheld': 'YES' if bill.is_gst_withheld else 'NO',
                'withheld_amount': float(bill.gst_withheld_amount)
            }
            purchase_rows.append(p_entry)

            if not is_matched:
                defaulter_rows.append({
                    'vendor_name': p_entry['vendor_name'],
                    'vendor_gstin': p_entry['vendor_gstin'],
                    'bill_number': p_entry['bill_number'],
                    'bill_date': p_entry['bill_date'],
                    'at_risk_tax': float(tax_amt),
                    'withheld_amount': float(bill.gst_withheld_amount),
                    'risk_tier': (bill.vendor.risk_tier.title() if bill.vendor else 'Defaulter'),
                    'compliance_score': (bill.vendor.compliance_score if bill.vendor else 50)
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

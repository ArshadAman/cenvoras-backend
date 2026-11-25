"""
1-Click Monthly CA Audit Pack ZIP Generator.
Bundles:
1. TallyPrime XML Vouchers (Accounting-Only, plus optional Full-Inventory)
2. Multi-tab GSTR-1 / GSTR-2B Audit Excel Workbook (via CAAuditPackGenerator)
3. Trial Balance Excel Workbook (via AccountingService.get_trial_balance)
4. GSTR Portal JSON Payload
5. README_FOR_CA.txt with 3-step TallyPrime import instructions
"""
from datetime import date, datetime
from decimal import Decimal
import io
import zipfile

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ledger.services import AccountingService
from .services_ca_pack import CAAuditPackGenerator, parse_date_safely
from .tally_exporter import TallyXMLExporter


def _build_trial_balance_excel(tenant, as_of_date: date) -> bytes:
    """Generate a formatted Excel workbook for the Trial Balance as of `as_of_date`."""
    AccountingService.get_or_create_default_accounts(tenant)
    tb_data = AccountingService.get_trial_balance(tenant, date_to=as_of_date)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Trial Balance"

    biz_name = getattr(tenant, 'business_name', '') or getattr(tenant, 'username', 'Company')

    # Title rows
    ws.merge_cells('A1:G1')
    ws['A1'] = f"{biz_name} — Trial Balance (As of {as_of_date.strftime('%d-%b-%Y')})"
    ws['A1'].font = Font(size=13, bold=True, color="FFFFFF")
    ws['A1'].fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    ws['A1'].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = [
        "Account Code",
        "Account Name",
        "Account Type",
        "Total Debit (₹)",
        "Total Credit (₹)",
        "Net Debit (₹)",
        "Net Credit (₹)",
    ]
    header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center" if col_idx >= 4 else "left")

    total_net_dr = Decimal('0.00')
    total_net_cr = Decimal('0.00')

    for acc_info in tb_data.get('accounts', []):
        acc = acc_info['account']
        dr = Decimal(str(acc_info.get('debit_total') or 0))
        cr = Decimal(str(acc_info.get('credit_total') or 0))
        net_dr = dr - cr if dr > cr else Decimal('0.00')
        net_cr = cr - dr if cr > dr else Decimal('0.00')

        total_net_dr += net_dr
        total_net_cr += net_cr

        ws.append([
            acc.code or "",
            acc.name,
            acc.account_type.title(),
            float(dr),
            float(cr),
            float(net_dr),
            float(net_cr),
        ])

    # Totals row
    total_dr = Decimal(str(tb_data.get('total_debits') or 0))
    total_cr = Decimal(str(tb_data.get('total_credits') or 0))

    ws.append([
        "",
        "TOTAL",
        "",
        float(total_dr),
        float(total_cr),
        float(total_net_dr),
        float(total_net_cr),
    ])
    total_row_idx = ws.max_row
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=total_row_idx, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
        if col_idx >= 4:
            cell.number_format = '#,##0.00'

    # Format numeric columns
    for row_idx in range(3, total_row_idx):
        for col_idx in range(4, 8):
            ws.cell(row=row_idx, column=col_idx).number_format = '#,##0.00'

    # Auto-fit column widths
    col_widths = [15, 34, 16, 18, 18, 18, 18]
    for idx, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_ca_readme(biz_name: str, start_date: date, end_date: date, include_inventory_xml: bool) -> str:
    """Create a concise, practical guide for the Chartered Accountant."""
    inv_line = (
        "2. Tally_Vouchers_Full_Inventory.xml   -> Full item, batch & godown vouchers (for Stock Audit)\n"
        if include_inventory_xml
        else ""
    )
    return f"""========================================================================
CENVORA MONTHLY CA AUDIT PACK
========================================================================
Business   : {biz_name}
Period     : {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}
Generated  : {datetime.now().strftime('%d-%b-%Y %H:%M')}

CONTENTS OF THIS ARCHIVE
------------------------------------------------------------------------
1. Tally_Vouchers_Accounting.xml       -> TallyPrime XML (Accounting Vouchers + Auto Masters)
{inv_line}3. GSTR_Audit_Registers.xlsx           -> Multi-tab Sales, Purchases, GSTR-2B & Summary
4. Trial_Balance.xlsx                  -> Double-Entry Trial Balance as of {end_date.strftime('%d-%b-%Y')}
5. GSTR_Portal_Payload.json            -> JSON payload for GST portal verification

HOW TO IMPORT INTO TALLYPRIME (60 SECONDS)
------------------------------------------------------------------------
Step 1: Open your client's company in TallyPrime.
Step 2: Press Alt+O (Import) -> Masters / Transactions -> Select 'Tally_Vouchers_Accounting.xml'.
Step 3: Verify Day Book & Trial Balance against 'Trial_Balance.xlsx'.

Note: Missing party ledgers (Sundry Debtors / Creditors) and GST tax ledgers
are automatically included inside the XML <All Masters> block.
========================================================================
"""


def generate_ca_audit_pack_zip(
    tenant,
    start_date=None,
    end_date=None,
    include_inventory_xml: bool = False,
) -> tuple[bytes, str]:
    """
    Generate the complete Monthly CA Audit Pack ZIP archive in memory.

    Returns:
        tuple[bytes, str]: (zip_bytes, suggested_filename)
    """
    today = date.today()
    parsed_start = parse_date_safely(start_date, date(today.year, today.month, 1))
    parsed_end = parse_date_safely(end_date, today)

    biz_name = getattr(tenant, 'business_name', '') or getattr(tenant, 'username', 'Company')
    safe_biz_name = "".join(c for c in biz_name if c.isalnum() or c in ('-', '_')).strip() or 'Company'
    period_slug = f"{parsed_start.strftime('%Y%m%d')}_to_{parsed_end.strftime('%Y%m%d')}"
    filename = f"{safe_biz_name}_CA_Audit_Pack_{period_slug}.zip"

    # 1. Generate Tally Accounting XML (respects saved CAMappingProfile automatically)
    accounting_xml = TallyXMLExporter(
        tenant=tenant,
        start_date=parsed_start,
        end_date=parsed_end,
        mode='accounting_only',
    ).generate_xml()

    # 2. Optional Full Inventory Tally XML
    inventory_xml = None
    if include_inventory_xml:
        inventory_xml = TallyXMLExporter(
            tenant=tenant,
            start_date=parsed_start,
            end_date=parsed_end,
            mode='full_inventory',
        ).generate_xml()

    # 3. Generate GSTR Excel & JSON via existing CAAuditPackGenerator
    audit_data = CAAuditPackGenerator.get_audit_data(tenant, parsed_start, parsed_end)
    gstr_excel_bytes = CAAuditPackGenerator.generate_excel(audit_data)
    gstr_json_str = CAAuditPackGenerator.generate_json(audit_data)

    # 4. Generate Trial Balance Excel
    tb_excel_bytes = _build_trial_balance_excel(tenant, parsed_end)

    # 5. Build README
    readme_text = _build_ca_readme(biz_name, parsed_start, parsed_end, include_inventory_xml)

    # Package everything into an in-memory ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Tally_Vouchers_Accounting.xml", accounting_xml.encode('utf-8'))
        if inventory_xml is not None:
            zf.writestr("Tally_Vouchers_Full_Inventory.xml", inventory_xml.encode('utf-8'))
        zf.writestr("GSTR_Audit_Registers.xlsx", gstr_excel_bytes)
        zf.writestr("Trial_Balance.xlsx", tb_excel_bytes)
        zf.writestr("GSTR_Portal_Payload.json", gstr_json_str.encode('utf-8'))
        zf.writestr("README_FOR_CA.txt", readme_text.encode('utf-8'))

    return zip_buffer.getvalue(), filename

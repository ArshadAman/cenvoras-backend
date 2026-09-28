import re
import json
import uuid
from decimal import Decimal
from datetime import datetime, date, timedelta
from django.db import transaction
from django.db.models import Sum, Q, F
from django.utils import timezone
from .models import PurchaseBill, PurchaseBillItem, Vendor
from .models_gst_shield import GSTR2BImport, GSTR2BRecord, VendorLegalNotice


def normalize_invoice_number(inv_num):
    r"""
    Normalizes invoice number by:
    - Uppercasing
    - Removing symbols (/ - _ . \ space)
    - Stripping leading zeros
    Example: 'INV/2026-0042' -> 'INV202642', '000123' -> '123'
    """
    if not inv_num:
        return ''
    cleaned = re.sub(r'[\s\/\-\_\.\\]', '', str(inv_num).strip().upper())
    # Strip leading zeros, but preserve single '0' if all zeroes
    stripped = cleaned.lstrip('0')
    return stripped if stripped else '0'


def parse_gst_date(date_str):
    """Parses various date formats returned in GST Portal files (DD-MM-YYYY, YYYY-MM-DD, etc.)"""
    if not date_str:
        return None
    if isinstance(date_str, (datetime, date)):
        return date_str if isinstance(date_str, date) else date_str.date()
    date_str = str(date_str).strip()
    for fmt in ('%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%b-%Y'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    return None


def calculate_purchase_bill_tax(bill):
    """Calculates total tax (CGST + SGST + IGST) and taxable base on a PurchaseBill"""
    items = bill.items.all()
    if not items.exists():
        # Fallback if no line items
        taxable_value = Decimal(str(bill.total_amount or 0))
        total_tax = Decimal('0.00')
        return taxable_value, total_tax, Decimal('0.00'), Decimal('0.00'), Decimal('0.00')

    taxable_value = Decimal('0.00')
    total_tax = Decimal('0.00')
    cgst = Decimal('0.00')
    sgst = Decimal('0.00')
    igst = Decimal('0.00')

    is_interstate = (bill.gst_treatment or '').lower() in ['interstate', 'igst', 'overseas', 'sez']

    for item in items:
        qty = Decimal(item.quantity or 0)
        price = Decimal(item.price or 0)
        disc = Decimal(item.discount or 0)
        tax_pct = Decimal(item.tax or 0)

        base = qty * price
        disc_amt = (base * disc) / Decimal('100')
        line_taxable = base - disc_amt
        line_tax = (line_taxable * tax_pct) / Decimal('100')

        taxable_value += line_taxable
        total_tax += line_tax

        if is_interstate:
            igst += line_tax
        else:
            half = line_tax / Decimal('2')
            cgst += half
            sgst += half

    return taxable_value, total_tax, cgst, sgst, igst


class GSTR2BParser:
    """Parses official GST Portal GSTR-2B files in JSON or Excel formats"""

    @classmethod
    def parse_json(cls, file_content):
        """
        Parses official GSTR-2B JSON payload.
        Expected structure: data.docdata.b2b or top-level b2b array.
        """
        if isinstance(file_content, (bytes, bytearray)):
            file_content = file_content.decode('utf-8', errors='ignore')
        
        payload = json.loads(file_content)
        records = []
        docdata = payload.get('data', {}).get('docdata', {}) or payload.get('docdata', {}) or payload

        # Extract return period and FY
        rtn_period = payload.get('data', {}).get('rtn_prd') or payload.get('rtn_prd') or payload.get('fp') or 'UNKNOWN'
        fy = payload.get('data', {}).get('fy') or payload.get('fy') or '2026-2027'

        # 1. Process B2B Invoices
        b2b_list = docdata.get('b2b', [])
        for supplier in b2b_list:
            ctin = (supplier.get('ctin') or '').strip().upper()
            trdnm = supplier.get('trdnm') or supplier.get('cmn') or ''

            for inv in supplier.get('inv', []):
                inum = str(inv.get('inum', '')).strip()
                idt = parse_gst_date(inv.get('idt'))
                inv_val = Decimal(str(inv.get('val', 0)))
                itcavl = (inv.get('itcavl') or 'Y').upper()

                taxable_val = Decimal('0.00')
                igst = Decimal('0.00')
                cgst = Decimal('0.00')
                sgst = Decimal('0.00')
                cess = Decimal('0.00')

                for item in inv.get('items', []):
                    taxable_val += Decimal(str(item.get('txval', 0)))
                    igst += Decimal(str(item.get('igst', 0)))
                    cgst += Decimal(str(item.get('cgst', 0)))
                    sgst += Decimal(str(item.get('sgst', 0)))
                    cess += Decimal(str(item.get('cess', 0)))

                records.append({
                    'supplier_gstin': ctin,
                    'supplier_trade_name': trdnm,
                    'invoice_number': inum,
                    'normalized_invoice_number': normalize_invoice_number(inum),
                    'invoice_type': 'B2B',
                    'invoice_date': idt,
                    'taxable_value': taxable_val,
                    'igst': igst,
                    'cgst': cgst,
                    'sgst': sgst,
                    'cess': cess,
                    'total_tax': igst + cgst + sgst + cess,
                    'itc_availability': itcavl,
                    'filing_date': parse_gst_date(inv.get('dt')),
                })

        # 2. Process Credit / Debit Notes (CDNR)
        cdnr_list = docdata.get('cdnr', [])
        for supplier in cdnr_list:
            ctin = (supplier.get('ctin') or '').strip().upper()
            trdnm = supplier.get('trdnm') or supplier.get('cmn') or ''

            for note in supplier.get('nt', []):
                nt_num = str(note.get('nt_num', '')).strip()
                nt_dt = parse_gst_date(note.get('nt_dt'))
                itcavl = (note.get('itcavl') or 'Y').upper()
                nt_typ = note.get('nt_typ', 'C')  # 'C' for Credit, 'D' for Debit

                taxable_val = Decimal('0.00')
                igst = Decimal('0.00')
                cgst = Decimal('0.00')
                sgst = Decimal('0.00')
                cess = Decimal('0.00')

                for item in note.get('items', []):
                    taxable_val += Decimal(str(item.get('txval', 0)))
                    igst += Decimal(str(item.get('igst', 0)))
                    cgst += Decimal(str(item.get('cgst', 0)))
                    sgst += Decimal(str(item.get('sgst', 0)))
                    cess += Decimal(str(item.get('cess', 0)))

                records.append({
                    'supplier_gstin': ctin,
                    'supplier_trade_name': trdnm,
                    'invoice_number': nt_num,
                    'normalized_invoice_number': normalize_invoice_number(nt_num),
                    'invoice_type': f'CDNR-{nt_typ}',
                    'invoice_date': nt_dt,
                    'taxable_value': taxable_val,
                    'igst': igst,
                    'cgst': cgst,
                    'sgst': sgst,
                    'cess': cess,
                    'total_tax': igst + cgst + sgst + cess,
                    'itc_availability': itcavl,
                    'filing_date': parse_gst_date(note.get('dt')),
                })

        return {
            'return_period': rtn_period,
            'financial_year': fy,
            'records': records
        }

    @classmethod
    def parse_excel(cls, file_obj):
        """
        Parses official GST Portal GSTR-2B Excel workbook (.xlsx).
        Reads sheets 'B2B', 'B2BA', 'CDNR'.
        """
        import openpyxl
        wb = openpyxl.load_workbook(file_obj, data_only=True)
        records = []
        
        sheet_names = [s for s in wb.sheetnames if any(k in s.upper() for k in ['B2B', 'CDNR'])]
        if not sheet_names:
            sheet_names = [wb.active.title]

        for sname in sheet_names:
            ws = wb[sname]
            header_row_idx = None
            header_map = {}

            # Find header row
            for row_idx in range(1, min(15, ws.max_row + 1)):
                row_vals = [str(cell.value or '').strip().lower() for cell in ws[row_idx]]
                if any('gstin' in v for v in row_vals) and any('invoice' in v or 'taxable' in v for v in row_vals):
                    header_row_idx = row_idx
                    for col_idx, val in enumerate(row_vals):
                        if val:
                            header_map[val] = col_idx
                    break

            if not header_row_idx:
                continue

            def get_col(*patterns):
                for p in patterns:
                    for h, col_idx in header_map.items():
                        if p in h:
                            return col_idx
                return None

            c_gstin = get_col('gstin of supplier', 'supplier gstin', 'gstin')
            c_name = get_col('trade/legal name', 'supplier name', 'legal name', 'name')
            c_inv = get_col('invoice number', 'document number', 'note number', 'inv no')
            c_type = get_col('invoice type', 'type')
            c_date = get_col('invoice date', 'document date', 'date')
            c_taxable = get_col('taxable value', 'taxable')
            c_igst = get_col('integrated tax', 'igst')
            c_cgst = get_col('central tax', 'cgst')
            c_sgst = get_col('state/ut tax', 'sgst')
            c_cess = get_col('cess')
            c_itc = get_col('itc availability', 'itc')
            c_filing = get_col('filing date', 'gstr-1/5 filing date')

            for r in range(header_row_idx + 1, ws.max_row + 1):
                row_cells = ws[r]
                gstin = str(row_cells[c_gstin].value or '').strip().upper() if c_gstin is not None else ''
                inv_no = str(row_cells[c_inv].value or '').strip() if c_inv is not None else ''

                if not gstin or not inv_no:
                    continue

                def num_val(idx):
                    if idx is None:
                        return Decimal('0.00')
                    v = row_cells[idx].value
                    try:
                        return Decimal(str(v or 0).replace(',', '').strip())
                    except Exception:
                        return Decimal('0.00')

                taxable = num_val(c_taxable)
                igst = num_val(c_igst)
                cgst = num_val(c_cgst)
                sgst = num_val(c_sgst)
                cess = num_val(c_cess)
                idt = parse_gst_date(row_cells[c_date].value) if c_date is not None else None
                fdate = parse_gst_date(row_cells[c_filing].value) if c_filing is not None else None
                itc = str(row_cells[c_itc].value or 'Y').strip().upper() if c_itc is not None else 'Y'
                name = str(row_cells[c_name].value or '').strip() if c_name is not None else ''

                records.append({
                    'supplier_gstin': gstin,
                    'supplier_trade_name': name,
                    'invoice_number': inv_no,
                    'normalized_invoice_number': normalize_invoice_number(inv_no),
                    'invoice_type': 'B2B',
                    'invoice_date': idt,
                    'taxable_value': taxable,
                    'igst': igst,
                    'cgst': cgst,
                    'sgst': sgst,
                    'cess': cess,
                    'total_tax': igst + cgst + sgst + cess,
                    'itc_availability': 'Y' if 'Y' in itc else 'N',
                    'filing_date': fdate,
                })

        return {
            'return_period': datetime.now().strftime('%m%Y'),
            'financial_year': f"{datetime.now().year}-{datetime.now().year + 1}",
            'records': records
        }


class GSTReconciliationService:
    """Core multi-tier reconciliation engine between ERP Purchase Bills and GSTR-2B"""

    TOLERANCE = Decimal('2.00')  # ₹2 rounding tolerance for tax/taxable

    @classmethod
    @transaction.atomic
    def run_reconciliation(cls, user, import_batch):
        """
        Reconciles all GSTR2BRecords in import_batch against the user's PurchaseBills.
        Updates match statuses, sets payment-withholding locks, and recalculates vendor scores.
        """
        tenant = getattr(user, 'active_tenant', user)
        records = GSTR2BRecord.objects.filter(import_batch=import_batch)
        bills = list(PurchaseBill.objects.filter(
            Q(created_by=tenant) | Q(created_by__parent=tenant)
        ).prefetch_related('items', 'vendor'))

        matched_count = 0
        probable_count = 0
        matched_bill_ids = set()

        # Build index of purchase bills for fast matching
        # Key: (vendor_gstin, normalized_bill_number)
        bills_by_key = {}
        for b in bills:
            v_gstin = (b.vendor_gstin or (b.vendor.gstin if b.vendor else '') or '').strip().upper()
            norm_num = normalize_invoice_number(b.bill_number)
            if v_gstin and norm_num:
                bills_by_key[(v_gstin, norm_num)] = b

        # Pass 1: Strict Exact Matching
        for rec in records:
            key = (rec.supplier_gstin, rec.normalized_invoice_number)
            bill = bills_by_key.get(key)

            if bill and bill.id not in matched_bill_ids:
                taxable, tax_amt, _, _, _ = calculate_purchase_bill_tax(bill)
                
                # Check tax and taxable values within tolerance
                tax_diff = abs(tax_amt - rec.total_tax)
                val_diff = abs(bill.total_amount - (rec.taxable_value + rec.total_tax))

                if tax_diff <= cls.TOLERANCE or val_diff <= cls.TOLERANCE:
                    rec.match_status = 'matched'
                    rec.matched_purchase_bill = bill
                    rec.save(update_fields=['match_status', 'matched_purchase_bill'])

                    bill.gstr2b_status = 'matched'
                    bill.is_gst_withheld = False
                    bill.gst_withheld_amount = Decimal('0.00')
                    bill.save(update_fields=['gstr2b_status', 'is_gst_withheld', 'gst_withheld_amount'])

                    matched_bill_ids.add(bill.id)
                    matched_count += 1
                    continue
                else:
                    # Same invoice number but tax mismatch > tolerance
                    rec.match_status = 'tax_mismatch'
                    rec.matched_purchase_bill = bill
                    rec.save(update_fields=['match_status', 'matched_purchase_bill'])

                    bill.gstr2b_status = 'disputed_tax'
                    bill.save(update_fields=['gstr2b_status'])
                    matched_bill_ids.add(bill.id)
                    probable_count += 1
                    continue

        # Pass 2: Probable Matching (Fuzzy search for same GSTIN + close taxable value)
        unmatched_records = records.filter(match_status='missing_in_books')
        unmatched_bills = [b for b in bills if b.id not in matched_bill_ids]

        for rec in unmatched_records:
            best_match = None
            for bill in unmatched_bills:
                v_gstin = (b.vendor_gstin or (b.vendor.gstin if b.vendor else '') or '').strip().upper()
                if v_gstin == rec.supplier_gstin:
                    taxable, tax_amt, _, _, _ = calculate_purchase_bill_tax(bill)
                    if abs(tax_amt - rec.total_tax) <= cls.TOLERANCE and abs(taxable - rec.taxable_value) <= cls.TOLERANCE:
                        best_match = bill
                        break

            if best_match and best_match.id not in matched_bill_ids:
                rec.match_status = 'probable'
                rec.matched_purchase_bill = best_match
                rec.save(update_fields=['match_status', 'matched_purchase_bill'])

                best_match.gstr2b_status = 'probable_match'
                best_match.save(update_fields=['gstr2b_status'])
                matched_bill_ids.add(best_match.id)
                probable_count += 1

        # Pass 3: Process Unmatched Bills -> Trigger Payment Withholding Lock
        for bill in bills:
            if bill.id not in matched_bill_ids:
                # Still missing in 2B
                taxable, tax_amt, _, _, _ = calculate_purchase_bill_tax(bill)
                bill.gstr2b_status = 'missing_in_2b'

                if not bill.gst_withholding_override:
                    # Automatically lock the GST portion to protect buyer
                    bill.is_gst_withheld = True
                    bill.gst_withheld_amount = tax_amt
                bill.save(update_fields=['gstr2b_status', 'is_gst_withheld', 'gst_withheld_amount'])

        # Update batch summary counts
        missing_books = records.filter(match_status='missing_in_books').count()
        import_batch.matched_count = matched_count
        import_batch.probable_count = probable_count
        import_batch.missing_in_books_count = missing_books
        import_batch.save(update_fields=['matched_count', 'probable_count', 'missing_in_books_count'])

        # Recalculate vendor scores
        cls.update_vendor_compliance_scores(tenant)

        return {
            'matched_count': matched_count,
            'probable_count': probable_count,
            'missing_in_books_count': missing_books,
            'at_risk_bills_count': len([b for b in bills if b.gstr2b_status == 'missing_in_2b'])
        }

    @classmethod
    def update_vendor_compliance_scores(cls, tenant):
        """Calculates dynamic 0-100 compliance scores and risk tiers for all vendors"""
        vendors = Vendor.objects.filter(Q(created_by=tenant) | Q(created_by__parent=tenant))

        for vendor in vendors:
            bills = PurchaseBill.objects.filter(vendor=vendor)
            if not bills.exists():
                vendor.compliance_score = 100
                vendor.risk_tier = 'safe'
                vendor.total_billed_itc = Decimal('0.00')
                vendor.total_reconciled_itc = Decimal('0.00')
                vendor.total_at_risk_itc = Decimal('0.00')
                vendor.save(update_fields=[
                    'compliance_score', 'risk_tier', 'total_billed_itc', 
                    'total_reconciled_itc', 'total_at_risk_itc'
                ])
                continue

            billed_itc = Decimal('0.00')
            reconciled_itc = Decimal('0.00')
            at_risk_itc = Decimal('0.00')

            for b in bills:
                _, tax_amt, _, _, _ = calculate_purchase_bill_tax(b)
                billed_itc += tax_amt

                if b.gstr2b_status == 'matched':
                    reconciled_itc += tax_amt
                elif b.gstr2b_status == 'missing_in_2b':
                    at_risk_itc += tax_amt

            vendor.total_billed_itc = billed_itc
            vendor.total_reconciled_itc = reconciled_itc
            vendor.total_at_risk_itc = at_risk_itc
            vendor.last_reconciliation_date = timezone.now()

            if billed_itc > 0:
                score = int(round((reconciled_itc / billed_itc) * 100))
            else:
                score = 100
            vendor.compliance_score = max(0, min(100, score))

            if vendor.compliance_score >= 85:
                vendor.risk_tier = 'safe'
            elif vendor.compliance_score >= 60:
                vendor.risk_tier = 'moderate_risk'
            else:
                vendor.risk_tier = 'defaulter'

            vendor.save(update_fields=[
                'compliance_score', 'risk_tier', 'total_billed_itc',
                'total_reconciled_itc', 'total_at_risk_itc', 'last_reconciliation_date'
            ])


class LegalNoticeService:
    """Generates statutory legal demand notices against delinquent suppliers under Section 16 CGST Act"""

    @classmethod
    def generate_notice(cls, user, vendor_id, period='Current FY', deadline_days=7):
        """Generates formal legal notice data for unfiled invoices of a specific vendor"""
        tenant = getattr(user, 'active_tenant', user)
        vendor = Vendor.objects.get(id=vendor_id, created_by=tenant)
        
        unmatched_bills = PurchaseBill.objects.filter(
            vendor=vendor,
            gstr2b_status='missing_in_2b'
        ).prefetch_related('items')

        if not unmatched_bills.exists():
            raise ValueError(f"No unmatched bills found for vendor '{vendor.name}'.")

        invoice_schedule = []
        total_invoiced = Decimal('0.00')
        total_itc_stuck = Decimal('0.00')

        for b in unmatched_bills:
            taxable, tax_amt, cgst, sgst, igst = calculate_purchase_bill_tax(b)
            total_invoiced += b.total_amount
            total_itc_stuck += tax_amt

            invoice_schedule.append({
                'bill_number': b.bill_number,
                'bill_date': b.bill_date.strftime('%d-%m-%Y') if b.bill_date else '-',
                'taxable_value': float(taxable),
                'cgst': float(cgst),
                'sgst': float(sgst),
                'igst': float(igst),
                'total_tax': float(tax_amt),
                'total_amount': float(b.total_amount)
            })

        deadline_date = (timezone.now() + timedelta(days=deadline_days)).date()
        notice_num = f"NOT/GST/{datetime.now().strftime('%Y%m')}/{uuid.uuid4().hex[:6].upper()}"

        buyer_name = getattr(user, 'business_name', '') or user.get_full_name() or user.username
        buyer_gstin = getattr(user, 'gstin', '') or 'UNREGISTERED'
        buyer_address = getattr(user, 'business_address', '') or getattr(user, 'address', '') or ''

        # Formal statutory legal notice body
        notice_body = f"""FORMAL LEGAL DEMAND NOTICE UNDER SECTION 16(2)(c) & (aa) OF THE CENTRAL GOODS AND SERVICES TAX ACT, 2017 READ WITH SECTION 73 & 74 OF THE INDIAN CONTRACT ACT, 1872

Ref No: {notice_num}
Date: {datetime.now().strftime('%d-%m-%Y')}

To,
M/s {vendor.name}
GSTIN: {vendor.gstin or 'N/A'}
Address: {vendor.address or 'N/A'}

From,
{buyer_name}
GSTIN: {buyer_gstin}
Address: {buyer_address}

SUBJECT: FINAL DEMAND NOTICE TO RECTIFY GSTR-1 FILING AND REFLECT INVOICES IN GSTR-2B FOR AN AGGREGATE INPUT TAX CREDIT (ITC) LOSS OF ₹{total_itc_stuck:,.2f}

Dear Sir / Madam,

1. We have procured commercial supplies from your establishment against the purchase invoices detailed in Schedule A below, during which GST amounting to ₹{total_itc_stuck:,.2f} was charged by you and paid/accounted by us in good faith.

2. STATUTORY BREACH UNDER SECTION 16(2)(aa) CGST ACT:
As per the provisions of Section 16(2)(aa) of the Central Goods and Services Tax Act, 2017, read with Rule 36(4) of the CGST Rules, 2017, Input Tax Credit (ITC) can strictly only be availed by a recipient if the details of the invoice or debit note have been furnished by the supplier in GSTR-1 / IFF and communicated to the recipient in Form GSTR-2B.

3. DELINQUENCY & FINANCIAL INJURY:
Upon reconciliation of our books of accounts with the auto-generated GSTR-2B statement on the GST Portal, the invoices listed in Schedule A DO NOT APPEAR in GSTR-2B due to non-filing, late filing, or erroneous GSTIN reporting on your end. Consequently, our Input Tax Credit of ₹{total_itc_stuck:,.2f} stands unlawfully blocked, forcing us to bear double cash outflow and facing scrutiny proceedings from the GST Department.

4. DEMAND FOR MANDATORY RECTIFICATION WITHIN {deadline_days} DAYS:
You are hereby called upon to immediately furnish/amend the said invoices in your GSTR-1 on or before {deadline_date.strftime('%d-%m-%Y')}, failing which:
  (a) An amount of ₹{total_itc_stuck:,.2f} plus statutory interest @ 18% per annum under Section 50 of the CGST Act will be permanently adjusted/withheld against all outstanding and future payments.
  (b) We shall proceed to file a formal complaint before the jurisdictional GST Anti-Evasion / Jurisdictional Commissionerate and initiate summary civil proceedings for recovery with interest, damages, and legal costs entirely at your risk and expense.

SCHEDULE A: PARTICULARS OF UNREFLECTED INVOICES
{json.dumps(invoice_schedule, indent=2)}

Yours faithfully,
For {buyer_name}

Authorized Signatory
"""

        notice = VendorLegalNotice.objects.create(
            vendor=vendor,
            notice_number=notice_num,
            period=period,
            total_unmatched_invoices=len(invoice_schedule),
            total_invoiced_amount=total_invoiced,
            total_itc_claimed_back=total_itc_stuck,
            statutory_deadline_date=deadline_date,
            invoices_payload=invoice_schedule,
            notice_text=notice_body,
            status='generated',
            created_by=tenant
        )

        # Formatted WhatsApp message link
        wa_text = (
            f"URGENT: Legal notice regarding missing GST in GSTR-2B from {buyer_name}.\n"
            f"Dear {vendor.name}, {len(invoice_schedule)} invoice(s) totaling ₹{total_itc_stuck:,.2f} in GST "
            f"are not reflecting in our GSTR-2B. Notice Ref: {notice_num}. "
            f"Kindly upload in GSTR-1 by {deadline_date.strftime('%d-%m-%Y')} to avoid payment withholding."
        )
        import urllib.parse
        wa_link = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_text)}"

        return {
            'notice_id': str(notice.id),
            'notice_number': notice_num,
            'vendor_name': vendor.name,
            'vendor_gstin': vendor.gstin,
            'total_invoices': len(invoice_schedule),
            'total_tax_at_risk': float(total_itc_stuck),
            'deadline_date': deadline_date.strftime('%d-%m-%Y'),
            'notice_text': notice_body,
            'whatsapp_link': wa_link,
            'invoices': invoice_schedule
        }

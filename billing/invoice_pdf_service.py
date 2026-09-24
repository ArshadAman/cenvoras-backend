import io
import math
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether, HRFlowable
)


def safe_hex_color(hex_str, default_hex):
    """Safely convert hex string to ReportLab Color with fallback."""
    if not hex_str or not isinstance(hex_str, str):
        return colors.HexColor(default_hex)
    cleaned = hex_str.strip()
    if not cleaned.startswith('#'):
        cleaned = f"#{cleaned}"
    if len(cleaned) not in [4, 7]:
        return colors.HexColor(default_hex)
    try:
        return colors.HexColor(cleaned)
    except Exception:
        return colors.HexColor(default_hex)


def number_to_words(number):
    """Convert amount to Indian numbering system words (Rupees & Paise, Lakhs, Crores)."""
    try:
        val = Decimal(str(number if number is not None else 0)).quantize(Decimal('0.01'))
    except Exception:
        return "Zero Rupees Only"

    if val == Decimal('0.00'):
        return "Zero Rupees Only"

    is_negative = val < 0
    val = abs(val)
    rupees = int(val)
    paise = int((val - Decimal(rupees)) * 100)

    units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
             "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
             "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def convert_upto_999(num):
        words = []
        if num >= 100:
            words.append(units[num // 100] + " Hundred")
            num %= 100
        if num >= 20:
            words.append(tens[num // 10])
            if num % 10:
                words.append(units[num % 10])
        elif num > 0:
            words.append(units[num])
        return " ".join(words)

    def convert_indian_number(n):
        if n == 0:
            return ""
        parts = []
        crore = n // 10000000
        if crore > 0:
            parts.append(convert_indian_number(crore) + " Crore")
            n %= 10000000
        lakh = n // 100000
        if lakh > 0:
            parts.append(convert_upto_999(lakh) + " Lakh")
            n %= 100000
        thousand = n // 1000
        if thousand > 0:
            parts.append(convert_upto_999(thousand) + " Thousand")
            n %= 1000
        if n > 0:
            parts.append(convert_upto_999(n))
        return " ".join(parts).strip()

    result_parts = []
    if is_negative:
        result_parts.append("Minus")

    if rupees > 0:
        rupees_str = convert_indian_number(rupees)
        result_parts.append(f"{rupees_str} {'Rupee' if rupees == 1 else 'Rupees'}")

    if paise > 0:
        paise_str = convert_upto_999(paise)
        paise_unit = 'Paisa' if paise == 1 else 'Paise'
        if rupees > 0:
            result_parts.append(f"and {paise_str} {paise_unit}")
        else:
            result_parts.append(f"{paise_str} {paise_unit}")

    result_parts.append("Only")
    return " ".join(result_parts).strip()



def generate_invoice_pdf(invoice_obj, tenant, document_type='invoice', template_data=None):
    """
    Generate a high-performance, theme-aware native vector PDF.
    - Preserves custom layout styles and color palettes
    - File size typically 30KB - 50KB (<100KB)
    - Automatically paginates multi-item invoices without slicing table rows
    - Repeats table headers across subsequent pages
    """
    buffer = io.BytesIO()

    # Template config extraction
    template = template_data or {}
    layout_type = template.get('layoutType') or template.get('layout', {}).get('layoutType', 'classic')
    template_colors = template.get('colors', {})

    # Resolve palette
    primary_color = safe_hex_color(template_colors.get('primary'), '#1a1a2e')
    secondary_color = safe_hex_color(template_colors.get('secondary'), '#16213e')
    accent_color = safe_hex_color(template_colors.get('accent'), '#0f3460')
    text_color = safe_hex_color(template_colors.get('text'), '#1e293b')
    light_text_color = safe_hex_color(template_colors.get('lightText'), '#64748b')
    table_header_bg = safe_hex_color(template_colors.get('tableHeader'), '#f8fafc')
    table_border_color = safe_hex_color(template_colors.get('tableBorder'), '#e2e8f0')
    total_row_bg = safe_hex_color(template_colors.get('totalRow'), '#1a1a2e')
    total_text_color = safe_hex_color(template_colors.get('totalText'), '#ffffff')

    # Document Setup (A4: 210mm x 297mm)
    margin = 10 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
    )
    content_width = 190 * mm

    styles = getSampleStyleSheet()

    # Custom Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=primary_color,
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=secondary_color,
    )
    body_bold = ParagraphStyle(
        'BodyBold',
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=text_color,
    )
    body_style = ParagraphStyle(
        'BodyStyle',
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=text_color,
    )
    small_muted = ParagraphStyle(
        'SmallMuted',
        fontName='Helvetica',
        fontSize=7.5,
        leading=10,
        textColor=light_text_color,
    )
    th_style = ParagraphStyle(
        'TableHeader',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=primary_color,
    )
    th_style_right = ParagraphStyle(
        'TableHeaderRight',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=2,
        textColor=primary_color,
    )
    td_style = ParagraphStyle(
        'TableCell',
        fontName='Helvetica',
        fontSize=8,
        leading=10.5,
        textColor=text_color,
    )
    td_style_bold = ParagraphStyle(
        'TableCellBold',
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10.5,
        textColor=text_color,
    )
    td_style_right = ParagraphStyle(
        'TableCellRight',
        fontName='Helvetica',
        fontSize=8,
        leading=10.5,
        alignment=2,
        textColor=text_color,
    )

    story = []

    # 1. Header Section
    is_quotation = (document_type == 'quotation')
    is_challan = (document_type == 'delivery_challan')
    if is_challan:
        doc_heading = "DELIVERY CHALLAN"
        doc_no_label = "Challan No"
    elif is_quotation:
        doc_heading = "PERFORMA INVOICE"
        doc_no_label = "Quotation No"
    else:
        doc_heading = "TAX INVOICE"
        doc_no_label = "Invoice No"

    biz_name = getattr(tenant, 'business_name', '') or getattr(tenant, 'username', '') or 'Business'
    biz_addr = getattr(tenant, 'business_address', '') or ''
    biz_gst = getattr(tenant, 'gstin', '') or ''
    biz_phone = getattr(tenant, 'phone', '') or ''
    biz_email = getattr(tenant, 'email', '') or ''

    inv_num = (
        getattr(invoice_obj, 'invoice_number', None)
        or getattr(invoice_obj, 'challan_number', None)
        or getattr(invoice_obj, 'quotation_number', None)
        or 'DRAFT'
    )
    inv_date = str(getattr(invoice_obj, 'invoice_date', None) or getattr(invoice_obj, 'quotation_date', None) or getattr(invoice_obj, 'date', ''))
    due_date = str(getattr(invoice_obj, 'due_date', '') or '')
    pos = getattr(invoice_obj, 'place_of_supply', '') or ''
    vehicle_no = getattr(invoice_obj, 'vehicle_number', '') or ''
    status_text = (getattr(invoice_obj, 'status', '') or '').upper()

    company_info_text = f"<b><font size=14 color='{primary_color.hexval()}'>{biz_name}</font></b><br/>"
    if biz_addr:
        company_info_text += f"{biz_addr}<br/>"
    if biz_gst:
        company_info_text += f"<b>GSTIN:</b> {biz_gst}<br/>"
    if biz_phone or biz_email:
        contacts = [c for c in [f"Ph: {biz_phone}" if biz_phone else "", f"Email: {biz_email}" if biz_email else ""] if c]
        company_info_text += f"{' | '.join(contacts)}<br/>"

    so_ref = ""
    if hasattr(invoice_obj, 'sales_order') and invoice_obj.sales_order:
        so_ref = getattr(invoice_obj.sales_order, 'order_number', '') or str(invoice_obj.sales_order_id or '')

    invoice_meta_text = (
        f"<font size=13 color='{primary_color.hexval()}'><b>{doc_heading}</b></font><br/>"
        f"<b>{doc_no_label}:</b> {inv_num}<br/>"
        f"<b>Date:</b> {inv_date}<br/>"
    )
    if so_ref:
        invoice_meta_text += f"<b>Sales Order:</b> {so_ref}<br/>"
    if vehicle_no:
        invoice_meta_text += f"<b>Vehicle No:</b> {vehicle_no}<br/>"
    if due_date:
        invoice_meta_text += f"<b>Due Date:</b> {due_date}<br/>"
    if pos:
        invoice_meta_text += f"<b>Place of Supply:</b> {pos}<br/>"

    header_table_data = [
        [
            Paragraph(company_info_text, body_style),
            Paragraph(invoice_meta_text, body_style),
        ]
    ]
    header_table = Table(header_table_data, colWidths=[115 * mm, 75 * mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6 * mm),
    ]))
    story.append(header_table)

    # Decorative Theme Divider
    story.append(HRFlowable(width="100%", thickness=1.5, color=primary_color, spaceAfter=4 * mm))

    # 2. Bill To / Ship To Section
    cust_name = getattr(invoice_obj, 'customer_name', '') or ''
    if not cust_name and getattr(invoice_obj, 'customer', None):
        cust_name = invoice_obj.customer.name

    bill_address = getattr(invoice_obj, 'customer_address', '') or ''
    if not bill_address and getattr(invoice_obj, 'customer', None):
        bill_address = invoice_obj.customer.address or ''

    ship_address = getattr(invoice_obj, 'delivery_address', '') or bill_address

    cust_gst = ''
    if getattr(invoice_obj, 'customer', None) and getattr(invoice_obj.customer, 'gstin', None):
        cust_gst = invoice_obj.customer.gstin

    bill_to_text = (
        f"<b><font color='{secondary_color.hexval()}'>BILL TO:</font></b><br/>"
        f"<b>{cust_name}</b><br/>"
        f"{bill_address}<br/>"
    )
    if cust_gst:
        bill_to_text += f"<b>GSTIN:</b> {cust_gst}<br/>"

    ship_to_text = (
        f"<b><font color='{secondary_color.hexval()}'>SHIP TO:</font></b><br/>"
        f"<b>{cust_name}</b><br/>"
        f"{ship_address}<br/>"
    )

    client_info_table = Table(
        [[Paragraph(bill_to_text, body_style), Paragraph(ship_to_text, body_style)]],
        colWidths=[95 * mm, 95 * mm]
    )
    client_info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 2 * mm),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')),
        ('BOX', (0, 0), (-1, -1), 0.5, table_border_color),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, table_border_color),
    ]))
    story.append(client_info_table)
    story.append(Spacer(1, 5 * mm))

    # 3. Line Items Table (with repeatRows=1 to never slice headers across pages)
    table_headers = [
        Paragraph("#", th_style),
        Paragraph("Item & Description", th_style),
        Paragraph("HSN/SAC", th_style),
        Paragraph("Qty", th_style_right),
        Paragraph("Rate (Rs.)", th_style_right),
        Paragraph("Disc (Rs.)", th_style_right),
        Paragraph("Tax (%)", th_style_right),
        Paragraph("Amount (Rs.)", th_style_right),
    ]
    items_data = [table_headers]

    raw_items = list(invoice_obj.items.all().select_related('product')) if hasattr(invoice_obj, 'items') else []
    
    subtotal = Decimal('0.00')
    total_tax = Decimal('0.00')

    for idx, item in enumerate(raw_items, start=1):
        p_name = getattr(item.product, 'name', '') or str(item.product)
        hsn = getattr(item, 'hsn_sac_code', '') or getattr(item.product, 'hsn_sac_code', '') or '—'
        qty = Decimal(str(getattr(item, 'quantity', 0) or 0))
        free_qty = Decimal(str(getattr(item, 'free_quantity', 0) or 0))
        price = Decimal(str(getattr(item, 'price', 0) or 0))
        discount = Decimal(str(getattr(item, 'discount', 0) or 0))
        tax_pct = Decimal(str(getattr(item, 'tax', 0) or 0))

        line_taxable = max(Decimal('0.00'), (qty * price) - discount)
        line_tax = (line_taxable * tax_pct) / Decimal('100.00')
        line_amount = Decimal(str(getattr(item, 'amount', None) or (line_taxable + line_tax)))

        subtotal += line_taxable
        total_tax += line_tax

        # Item title + scheme badge if any
        desc_text = f"<b>{p_name}</b>"
        if free_qty > 0:
            desc_text += f"<br/><font color='#16a34a' size=7.5><b>+ {int(free_qty)} Free (Promotional Offer)</b></font>"

        qty_display = f"{int(qty) if qty % 1 == 0 else qty}"
        if getattr(item, 'unit', None):
            qty_display += f" {item.unit}"

        row = [
            Paragraph(str(idx), td_style),
            Paragraph(desc_text, td_style),
            Paragraph(str(hsn), td_style),
            Paragraph(qty_display, td_style_right),
            Paragraph(f"{price:,.2f}", td_style_right),
            Paragraph(f"{discount:,.2f}" if discount > 0 else "—", td_style_right),
            Paragraph(f"{tax_pct:.1f}%", td_style_right),
            Paragraph(f"{line_amount:,.2f}", td_style_bold),
        ]
        items_data.append(row)

    # Column widths totaling exactly 190mm
    col_widths = [8 * mm, 62 * mm, 20 * mm, 18 * mm, 24 * mm, 18 * mm, 16 * mm, 24 * mm]

    table_style_commands = [
        ('REPEATROWS', (0, 0), (-1, 0)),  # Table header repeats on subsequent pages
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, 0), table_header_bg),
        ('TEXTCOLOR', (0, 0), (-1, 0), primary_color),
        ('GRID', (0, 0), (-1, -1), 0.5, table_border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 2 * mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2 * mm),
    ]

    # Alternate row striping for enhanced visual polish
    for r_idx in range(1, len(items_data)):
        if r_idx % 2 == 0:
            table_style_commands.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.HexColor('#fcfcfc')))

    items_table = Table(items_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle(table_style_commands))
    story.append(items_table)
    story.append(Spacer(1, 4 * mm))

    # 4. Summary and Tax Block (Kept together to avoid splitting)
    round_off = Decimal(str(getattr(invoice_obj, 'round_off', 0) or 0))
    grand_total = Decimal(str(getattr(invoice_obj, 'total_amount', subtotal + total_tax + round_off)))

    # Determine Intra-state vs Inter-state
    seller_state = getattr(tenant, 'state', None)
    customer_state = getattr(invoice_obj, 'place_of_supply', None) or (invoice_obj.customer.state if getattr(invoice_obj, 'customer', None) else None)
    is_interstate = bool(seller_state and customer_state and seller_state.strip().upper() != customer_state.strip().upper())

    summary_rows = [
        [Paragraph("Taxable Subtotal:", body_style), Paragraph(f"Rs. {subtotal:,.2f}", td_style_right)],
    ]
    if total_tax > 0:
        if is_interstate:
            summary_rows.append([Paragraph("Output IGST:", body_style), Paragraph(f"Rs. {total_tax:,.2f}", td_style_right)])
        else:
            half_tax = total_tax / Decimal('2.0')
            summary_rows.append([Paragraph("Output CGST:", body_style), Paragraph(f"Rs. {half_tax:,.2f}", td_style_right)])
            summary_rows.append([Paragraph("Output SGST:", body_style), Paragraph(f"Rs. {half_tax:,.2f}", td_style_right)])

    if round_off != 0:
        summary_rows.append([Paragraph("Round Off:", body_style), Paragraph(f"Rs. {round_off:,.2f}", td_style_right)])

    summary_rows.append([
        Paragraph(f"<b><font size=10 color='{total_text_color.hexval()}'>TOTAL AMOUNT:</font></b>", body_bold),
        Paragraph(f"<b><font size=10 color='{total_text_color.hexval()}'>Rs. {grand_total:,.2f}</font></b>", td_style_right)
    ])

    summary_table = Table(summary_rows, colWidths=[45 * mm, 45 * mm])
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 2 * mm),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor('#eeeeee')),
        ('BACKGROUND', (0, -1), (-1, -1), total_row_bg),
    ]))

    # Amount in Words & Bank Info on Left Side
    amount_words = number_to_words(grand_total)
    left_notes = f"<b>Amount in Words:</b><br/><i>{amount_words}</i><br/><br/>"

    # Fetch InvoiceSettings for bank details / terms if available
    inv_settings = getattr(tenant, 'invoice_settings', None)
    terms_text = inv_settings.terms_conditions if inv_settings and inv_settings.terms_conditions else "1. Goods once sold will not be taken back.<br/>2. Payment is due upon receipt."
    left_notes += f"<b>Terms & Conditions:</b><br/>{terms_text}"

    totals_composite_table = Table(
        [[Paragraph(left_notes, small_muted), summary_table]],
        colWidths=[100 * mm, 90 * mm]
    )
    totals_composite_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 1 * mm),
    ]))

    # Keep footer and summary block together
    footer_block = []
    footer_block.append(totals_composite_table)
    footer_block.append(Spacer(1, 6 * mm))

    # Signatory Block
    sig_text = (
        f"For <b>{biz_name}</b><br/><br/><br/><br/>"
        "<b>Authorized Signatory</b>"
    )
    sig_table = Table(
        [[Paragraph("<font color='#888'>This is a computer generated invoice.</font>", small_muted),
          Paragraph(sig_text, ParagraphStyle('Sig', parent=body_style, alignment=2))]],
        colWidths=[120 * mm, 70 * mm]
    )
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('PADDING', (0, 0), (-1, -1), 0),
    ]))
    footer_block.append(sig_table)

    story.append(KeepTogether(footer_block))

    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

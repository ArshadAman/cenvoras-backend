"""
TallyPrime / Tally.ERP 9 XML Master & DayBook Ingestion Parser (users/tally_importer.py).

Handles real-world Tally XML quirks:
- UTF-16LE / UTF-16BE / UTF-8 BOM decoding
- Stripping illegal XML 1.0 control characters and &#4; / &#x04; entities emitted by Tally
- Extracting <LEDGER> (Sundry Debtors & Creditors with GSTIN, State, Phone, Opening Balance)
- Extracting <STOCKITEM> (Base/Additional units, HSN, GST%, Opening Balance/Rate, Batch Allocations)
- Extracting <VOUCHER> transactions (Sales, Purchase, Credit Note, Debit Note, Receipt, Payment)
  for HistoricalDocumentArchive indexing (Task 2.5)
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

_ILLEGAL_XML_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_ILLEGAL_ENTITY_RE = re.compile(r'&#(?:0?[0-8]|1[12]|1[4-9]|2[0-9]|3[01]|x0?[0-8]|x0?[bBcCeEfF]|x1[0-9a-fA-F]);')


def decode_tally_xml_bytes(raw_bytes: bytes) -> str:
    """Decode Tally XML bytes handling UTF-16 BOMs and strip illegal XML control chars."""
    if not raw_bytes:
        return ''

    if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
        text = raw_bytes.decode('utf-16', errors='replace')
    elif raw_bytes.startswith(b'\xef\xbb\xbf'):
        text = raw_bytes.decode('utf-8-sig', errors='replace')
    else:
        # Check for UTF-16 without BOM (null bytes every second char)
        if b'\x00<' in raw_bytes[:100] or b'<\x00' in raw_bytes[:100]:
            text = raw_bytes.decode('utf-16-le', errors='replace')
        else:
            try:
                text = raw_bytes.decode('utf-8')
            except UnicodeDecodeError:
                text = raw_bytes.decode('cp1252', errors='replace')

    # Strip encoding declaration if decoded from UTF-16 so ElementTree doesn't choke
    text = re.sub(r'encoding=["\']UTF-16["\']', 'encoding="utf-8"', text, flags=re.IGNORECASE)
    text = _ILLEGAL_ENTITY_RE.sub('', text)
    text = _ILLEGAL_XML_CHARS_RE.sub('', text)
    return text.strip()


def _text(elem, tag: str, default: str = '') -> str:
    if elem is None:
        return default
    child = elem.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _parse_tally_qty(qty_str: str) -> Decimal:
    """Parse ' 100 pcs' or '-50.5 box' -> Decimal('100')."""
    if not qty_str:
        return Decimal('0.000')
    m = re.search(r'-?\d+(?:\.\d+)?', str(qty_str).replace(',', ''))
    if not m:
        return Decimal('0.000')
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return Decimal('0.000')


def _parse_tally_rate(rate_str: str) -> Decimal:
    """Parse '120.00/pcs' -> Decimal('120.00')."""
    if not rate_str:
        return Decimal('0.00')
    part = str(rate_str).split('/')[0].replace(',', '').strip()
    m = re.search(r'-?\d+(?:\.\d+)?', part)
    if not m:
        return Decimal('0.00')
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return Decimal('0.00')


def _parse_tally_date_iso(date_str: str) -> str:
    """Convert Tally 'YYYYMMDD' -> 'YYYY-MM-DD'."""
    clean = str(date_str or '').strip()
    if len(clean) == 8 and clean.isdigit():
        try:
            return datetime.strptime(clean, '%Y%m%d').date().isoformat()
        except ValueError:
            pass
    return clean


def _extract_stockitem_gst_rate(item_elem) -> Decimal:
    """Extract GST % from Tally <GSTDETAILS.LIST> -> <RATEDETAILS.LIST>."""
    # Look for IGST/Integrated Tax rate first, or sum CGST + SGST
    igst_rate = None
    cgst_rate = Decimal('0')
    sgst_rate = Decimal('0')

    for rate_detail in item_elem.findall('.//RATEDETAILS.LIST'):
        duty_head = _text(rate_detail, 'GSTRATEDUTYHEAD').lower()
        rate_val = _parse_tally_qty(_text(rate_detail, 'GSTRATE'))
        if 'integrated' in duty_head or 'igst' in duty_head:
            igst_rate = rate_val
        elif 'central' in duty_head or 'cgst' in duty_head:
            cgst_rate = rate_val
        elif 'state' in duty_head or 'sgst' in duty_head:
            sgst_rate = rate_val

    if igst_rate is not None and igst_rate > 0:
        return igst_rate
    if cgst_rate > 0 or sgst_rate > 0:
        return cgst_rate + sgst_rate
    return Decimal('0')


def parse_tally_xml(raw_bytes: bytes) -> dict:
    """
    Parse TallyPrime Master.xml / DayBook.xml bytes into:
      {
        'products': [...],
        'parties': [...],
        'batches': [...],
        'historical_vouchers': [...],
      }
    ready for MigrationValidationGate and HistoricalDocumentArchive.
    """
    xml_text = decode_tally_xml_bytes(raw_bytes)
    if not xml_text:
        return {'products': [], 'parties': [], 'batches': [], 'historical_vouchers': []}

    root = ET.fromstring(xml_text)

    products = []
    parties = []
    batches = []
    historical_vouchers = []

    # 1. Parse Ledgers (Sundry Debtors / Sundry Creditors)
    for ledger in root.findall('.//LEDGER'):
        name = (ledger.attrib.get('NAME') or _text(ledger, 'NAME')).strip()
        if not name:
            continue

        parent = _text(ledger, 'PARENT').lower()
        if 'debtor' in parent:
            party_type = 'customer'
        elif 'creditor' in parent:
            party_type = 'vendor'
        else:
            # Skip internal GL accounts (Sales, Duties & Taxes, etc.) from party import
            continue

        addr_lines = [a.text.strip() for a in ledger.findall('.//ADDRESS.LIST/ADDRESS') if a.text and a.text.strip()]
        address = ', '.join(addr_lines)

        gstin = _text(ledger, 'PARTYGSTIN') or _text(ledger, 'GSTIN')
        state = _text(ledger, 'LEDSTATENAME') or _text(ledger, 'STATENAME')
        phone = _text(ledger, 'LEDGERMOBILE') or _text(ledger, 'LEDGERPHONE')
        email = _text(ledger, 'EMAIL')

        # In Tally, Debit opening balance on a Debtor is negative (-5000.00) -> Customer owes us +5000.00
        raw_ob = _parse_tally_qty(_text(ledger, 'OPENINGBALANCE'))
        opening_balance = -raw_ob if party_type == 'customer' else raw_ob

        parties.append({
            'name': name,
            'party_type': party_type,
            'gstin': gstin,
            'phone': phone,
            'email': email,
            'address': address,
            'state': state,
            'opening_balance': opening_balance,
        })

    # 2. Parse Stock Items & Batch Opening Stock
    for item in root.findall('.//STOCKITEM'):
        name = (item.attrib.get('NAME') or _text(item, 'NAME')).strip()
        if not name:
            continue

        unit = _text(item, 'BASEUNITS') or 'pcs'
        secondary_unit = _text(item, 'ADDITIONALUNITS') or ''
        conv_raw = _parse_tally_qty(_text(item, 'CONVERSION'))
        conversion_factor = int(conv_raw) if conv_raw and conv_raw >= 1 else 1

        hsn = _text(item, 'HSNCODE') or _text(item, './/HSNDETAILS.LIST/HSNCODE')
        gst_rate = _extract_stockitem_gst_rate(item)
        opening_rate = _parse_tally_rate(_text(item, 'OPENINGRATE'))
        opening_qty = abs(_parse_tally_qty(_text(item, 'OPENINGBALANCE')))

        products.append({
            'name': name,
            'item_code': _text(item, 'PARTNO') or '',
            'hsn_sac_code': hsn,
            'manufacturer': _text(item, 'PARENT'),
            'unit': unit.lower(),
            'secondary_unit': secondary_unit.lower() if secondary_unit else None,
            'conversion_factor': conversion_factor,
            'price': opening_rate,
            'tax': gst_rate,
        })

        batch_allocs = item.findall('.//BATCHALLOCATIONS.LIST')
        if batch_allocs:
            for ba in batch_allocs:
                qty = abs(_parse_tally_qty(_text(ba, 'OPENINGBALANCE')))
                if qty <= 0:
                    continue
                batches.append({
                    'product_name': name,
                    'batch_number': _text(ba, 'BATCHNAME') or 'DEFAULT',
                    'warehouse_name': _text(ba, 'GODOWNNAME') or 'Main Godown',
                    'expiry_date': _parse_tally_date_iso(_text(ba, 'EXPIRYPERIOD')),
                    'quantity': qty,
                    'cost_price': _parse_tally_rate(_text(ba, 'OPENINGRATE')) or opening_rate,
                })
        elif opening_qty > 0:
            batches.append({
                'product_name': name,
                'batch_number': 'DEFAULT',
                'warehouse_name': 'Main Godown',
                'quantity': opening_qty,
                'cost_price': opening_rate,
            })

    # 3. Parse Vouchers (DayBook Historical Archive)
    for vch in root.findall('.//VOUCHER'):
        vch_type = (_text(vch, 'VOUCHERTYPENAME') or vch.attrib.get('VCHTYPE') or '').strip()
        vch_no = _text(vch, 'VOUCHERNUMBER')
        vch_date = _parse_tally_date_iso(_text(vch, 'DATE'))
        party_name = _text(vch, 'PARTYLEDGERNAME')

        # Find party ledger amount for total
        total_amount = Decimal('0.00')
        for le in vch.findall('.//ALLLEDGERENTRIES.LIST'):
            if _text(le, 'LEDGERNAME') == party_name:
                total_amount = abs(_parse_tally_qty(_text(le, 'AMOUNT')))
                break

        line_items = []
        for inv_entry in vch.findall('.//ALLINVENTORYENTRIES.LIST'):
            line_items.append({
                'item_name': _text(inv_entry, 'STOCKITEMNAME'),
                'quantity': float(abs(_parse_tally_qty(_text(inv_entry, 'BILLEDQTY') or _text(inv_entry, 'ACTUALQTY')))),
                'rate': float(_parse_tally_rate(_text(inv_entry, 'RATE'))),
                'amount': float(abs(_parse_tally_qty(_text(inv_entry, 'AMOUNT')))),
            })

        if vch_no or party_name:
            historical_vouchers.append({
                'source': 'tally',
                'voucher_type': vch_type or 'Sales',
                'document_number': vch_no,
                'document_date': vch_date,
                'party_name': party_name,
                'total_amount': float(total_amount),
                'items': line_items,
            })

    return {
        'products': products,
        'parties': parties,
        'batches': batches,
        'historical_vouchers': historical_vouchers,
    }

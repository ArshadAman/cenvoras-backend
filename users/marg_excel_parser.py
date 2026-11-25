"""
Marg ERP 9+ Multi-Sheet Excel & CSV Ingestion Parser (Tier 1 & Tier 2).

Tier 1: Automatic Detection & Normalization
  - Reads .xlsx (openpyxl) and .csv (multi-encoding fallback)
  - Detects Marg export type: 'products', 'parties', or 'batches'
  - Maps native Marg headers (P_NAME, PACKING, RATE_A/B/C, MRP, PUR_RATE,
    BATCH_NO, EXP_DATE, DRUG_LIC_1/2, FSSAI, OP_BAL, STATION, etc.)

Tier 2: Visual Column Mapper Preview
  - preview_marg_spreadsheet() extracts headers, first 10 sample rows,
    detected entity type, and suggested header->field mapping so any custom
    Marg report layout can be adjusted in UI and re-parsed via custom_mapping.
"""
import csv
import io
import re
from typing import Any

import openpyxl

# Canonical field -> known Marg / Tally / Excel header aliases (lowercased, stripped of punctuation)
FIELD_ALIASES = {
    # Product fields
    'item_code': {'itemcode', 'item_code', 'pcode', 'p_code', 'code', 'sku', 'barcode'},
    'name': {'pname', 'p_name', 'itemname', 'item_name', 'productname', 'product_name', 'partyname', 'party_name', 'ledgername', 'ledger_name', 'customername', 'vendorname', 'description', 'name', 'particulars'},
    'packing': {'packing', 'pack', 'packsize', 'pack_size'},
    'unit': {'unit', 'uom', 'baseunit', 'base_unit'},
    'hsn_sac_code': {'hsn', 'hsncode', 'hsn_code', 'hsnsac', 'hsn_sac_code', 'sac'},
    'manufacturer': {'company', 'compname', 'comp_name', 'manufacturer', 'mfr', 'brand', 'mfg'},
    'tax': {'tax', 'gst', 'gstper', 'gst_per', 'taxper', 'tax_per', 'igst', 'gst%'},
    'price': {'purrate', 'pur_rate', 'purchaserate', 'purchase_rate', 'cost', 'costprice', 'cost_price', 'prate', 'p_rate'},
    'sale_price': {'salerate', 'sale_rate', 'srate', 's_rate', 'sellingprice', 'selling_price', 'rate'},
    'mrp': {'mrp', 'maxretailprice'},
    'rate_a': {'ratea', 'rate_a', 'retailrate', 'retail_rate'},
    'rate_b': {'rateb', 'rate_b', 'wholesalerate', 'wholesale_rate'},
    'rate_c': {'ratec', 'rate_c', 'specialrate', 'special_rate', 'hospitalrate'},

    # Batch / Stock fields
    'batch_number': {'batchno', 'batch_no', 'batch', 'batchnumber', 'batch_number', 'lotno', 'lot_no'},
    'expiry_date': {'expdate', 'exp_date', 'expiry', 'expirydate', 'expiry_date', 'exp'},
    'quantity': {'qty', 'quantity', 'stock', 'clstock', 'cl_stock', 'closingstock', 'openingstock', 'opstock', 'op_stock', 'balqty', 'balanceqty'},
    'warehouse_name': {'godown', 'godownname', 'warehouse', 'warehousename', 'store', 'rack', 'location'},

    # Party fields
    'party_type': {'partytype', 'party_type', 'group', 'accountgroup', 'parent', 'type'},
    'gstin': {'gstin', 'gstno', 'gst_no', 'gstnumber', 'partygstin'},
    'phone': {'phone', 'mobile', 'mobileno', 'mobile_no', 'contact', 'tel', 'whatsapp'},
    'email': {'email', 'emailid', 'email_id'},
    'address': {'address', 'addr', 'address1'},
    'state': {'state', 'statename', 'state_name', 'station', 'city'},
    'opening_balance': {'openingbalance', 'opening_balance', 'opbal', 'op_bal', 'balance', 'clbal'},
    'dl_number_20b': {'dlno1', 'dl_no_1', 'druglic1', 'drug_lic_1', 'dl20b', 'dl_20b', 'dlno', 'dl_no'},
    'dl_number_21b': {'dlno2', 'dl_no_2', 'druglic2', 'drug_lic_2', 'dl21b', 'dl_21b'},
    'dl_expiry_date': {'dlexp', 'dl_exp', 'dlexpiry', 'dl_expiry_date'},
    'fssai_number': {'fssai', 'fssaino', 'fssai_no', 'fssai_number'},
    'default_price_tier': {'pricetier', 'price_tier', 'defaultrate', 'default_rate', 'ratetype', 'rate_type'},
}


def _norm_header(h: Any) -> str:
    """Normalize a column header for fuzzy matching."""
    return re.sub(r'[^a-z0-9_]', '', str(h or '').strip().lower())


def _decode_csv_bytes(raw_bytes: bytes) -> str:
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode('utf-8', errors='replace')


def _read_tabular_rows(file_bytes: bytes, filename: str) -> tuple[list[str], list[list[Any]]]:
    """Read headers and data rows from .xlsx or .csv bytes."""
    lower = (filename or '').lower().strip()
    if lower.endswith('.csv'):
        text = _decode_csv_bytes(file_bytes)
        reader = csv.reader(io.StringIO(text))
        all_rows = [row for row in reader if any(str(c).strip() for c in row)]
    else:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        all_rows = []
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() != '' for c in row):
                all_rows.append(list(row))

    if not all_rows:
        return [], []

    headers = [str(h or '').strip() for h in all_rows[0]]
    data_rows = all_rows[1:]
    return headers, data_rows


def suggest_column_mapping(headers: list[str]) -> dict[str, str]:
    """
    Map raw spreadsheet headers -> canonical Cenvora field names.
    Returns dict {raw_header: canonical_field}.
    """
    mapping = {}
    used_fields = set()
    for raw_h in headers:
        norm = _norm_header(raw_h)
        if not norm:
            continue
        # Exact alias match first
        matched_field = None
        for canonical, aliases in FIELD_ALIASES.items():
            if norm in aliases or norm == canonical:
                matched_field = canonical
                break
        if matched_field and matched_field not in used_fields:
            mapping[raw_h] = matched_field
            used_fields.add(matched_field)
    return mapping


def detect_marg_sheet_type(mapped_fields: set[str]) -> str:
    """
    Heuristically detect whether a Marg sheet is 'batches', 'parties', or 'products'.
    """
    if {'dl_number_20b', 'dl_number_21b', 'opening_balance', 'gstin', 'phone'} & mapped_fields:
        return 'parties'
    # Consolidated Item+Batch sheet (has packing, tax, or hsn) should be parsed as 'products'
    # so both products and batches are emitted.
    if {'packing', 'tax', 'hsn_sac_code'} & mapped_fields:
        return 'products'
    if {'batch_number', 'expiry_date'} & mapped_fields or ('quantity' in mapped_fields and 'mrp' in mapped_fields):
        return 'batches'
    return 'products'


def preview_marg_spreadsheet(file_bytes: bytes, filename: str) -> dict:
    """
    Tier 2 Preview: Returns detected entity type, raw headers, suggested mapping,
    and first 10 sample rows for visual column mapping UI.
    """
    headers, data_rows = _read_tabular_rows(file_bytes, filename)
    suggested = suggest_column_mapping(headers)
    detected_type = detect_marg_sheet_type(set(suggested.values()))

    sample_rows = []
    for row in data_rows[:10]:
        row_dict = {}
        for idx, h in enumerate(headers):
            val = row[idx] if idx < len(row) else ''
            row_dict[h] = '' if val is None else str(val)
        sample_rows.append(row_dict)

    return {
        'filename': filename,
        'detected_type': detected_type,
        'total_rows': len(data_rows),
        'headers': headers,
        'suggested_mapping': suggested,
        'sample_rows': sample_rows,
    }


def parse_marg_spreadsheet(
    file_bytes: bytes,
    filename: str,
    sheet_type: str | None = None,
    custom_mapping: dict[str, str] | None = None,
) -> dict[str, list[dict]]:
    """
    Parse a Marg ERP Excel/CSV export into normalized {'products': [], 'parties': [], 'batches': []}
    dicts ready for MigrationValidationGate.dry_run() and .commit().
    """
    headers, data_rows = _read_tabular_rows(file_bytes, filename)
    if not headers:
        return {'products': [], 'parties': [], 'batches': []}

    mapping = suggest_column_mapping(headers)
    if custom_mapping and isinstance(custom_mapping, dict):
        mapping.update({k: v for k, v in custom_mapping.items() if v})

    resolved_type = sheet_type or detect_marg_sheet_type(set(mapping.values()))

    products = []
    parties = []
    batches = []

    for row in data_rows:
        record = {}
        for idx, raw_h in enumerate(headers):
            canonical = mapping.get(raw_h)
            if canonical and idx < len(row):
                record[canonical] = row[idx]

        if resolved_type == 'products':
            # Default sale_price to rate_a if sale_price isn't explicitly mapped
            if record.get('sale_price') in (None, '') and record.get('rate_a') not in (None, ''):
                record['sale_price'] = record['rate_a']
            products.append(record)

            # If consolidated Marg Item sheet also includes batch/stock columns, emit batch row too
            if record.get('batch_number') or record.get('quantity') not in (None, ''):
                batches.append({
                    'product_name': record.get('name'),
                    'item_code': record.get('item_code'),
                    'batch_number': record.get('batch_number') or 'DEFAULT',
                    'expiry_date': record.get('expiry_date'),
                    'warehouse_name': record.get('warehouse_name') or 'Main Godown',
                    'quantity': record.get('quantity', 0),
                    'mrp': record.get('mrp', 0),
                    'cost_price': record.get('price', 0),
                    'sale_price': record.get('sale_price', 0),
                    'rate_a': record.get('rate_a', 0),
                    'rate_b': record.get('rate_b', 0),
                    'rate_c': record.get('rate_c', 0),
                })

        elif resolved_type == 'parties':
            # Map Marg party group names ('Sundry Creditors' -> 'vendor', 'Sundry Debtors' -> 'customer')
            raw_pt = str(record.get('party_type') or '').lower()
            if 'creditor' in raw_pt or 'vendor' in raw_pt or 'supplier' in raw_pt:
                record['party_type'] = 'vendor'
            elif 'both' in raw_pt:
                record['party_type'] = 'both'
            else:
                record['party_type'] = 'customer'
            parties.append(record)

        elif resolved_type == 'batches':
            # Map 'name' -> 'product_name' for batch rows
            if not record.get('product_name') and record.get('name'):
                record['product_name'] = record['name']
            if record.get('sale_price') in (None, '') and record.get('rate_a') not in (None, ''):
                record['sale_price'] = record['rate_a']
            if record.get('cost_price') in (None, '') and record.get('price') not in (None, ''):
                record['cost_price'] = record['price']
            batches.append(record)

    return {
        'products': products,
        'parties': parties,
        'batches': batches,
    }

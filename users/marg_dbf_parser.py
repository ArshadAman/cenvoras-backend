"""
Marg ERP 9+ Tier 3 Raw FoxPro DBF Folder Parser (users/marg_dbf_parser.py).

Accepts a zipped Marg DATA directory containing .DBF files (e.g. ITEM.DBF,
LEDGER.DBF, BATCH.DBF), safely extracts them into a temporary directory,
parses records with dbfread (ignoring missing .FPT memo files and handling
cp1252 encoding), and outputs normalized {'products', 'parties', 'batches'}
ready for MigrationValidationGate.
"""
import io
import os
import tempfile
import zipfile

from dbfread import DBF

from .marg_excel_parser import (
    FIELD_ALIASES,
    _norm_header,
    detect_marg_sheet_type,
)

# Additional FoxPro 8.3 short column names commonly found in Marg DBFs
_DBF_EXTRA_ALIASES = {
    'item_code': {'code', 'itcode', 'it_code', 'pcode'},
    'name': {'itname', 'it_name', 'pname', 'lname', 'acname', 'ac_name'},
    'packing': {'pack', 'packing'},
    'price': {'prate', 'purrate', 'cost'},
    'sale_price': {'srate', 'salerate'},
    'rate_a': {'ratea', 'rate_a'},
    'rate_b': {'rateb', 'rate_b'},
    'rate_c': {'ratec', 'rate_c'},
    'mrp': {'mrp'},
    'tax': {'tax', 'gst', 'gstper', 'sgst'},
    'hsn_sac_code': {'hsn', 'hsncode'},
    'batch_number': {'batch', 'batchno'},
    'expiry_date': {'expdt', 'expdate', 'exp'},
    'quantity': {'clqty', 'opqty', 'qty', 'stock', 'bal'},
    'opening_balance': {'opbal', 'clbal', 'bal'},
    'dl_number_20b': {'dl1', 'dlno1', 'dl20b'},
    'dl_number_21b': {'dl2', 'dlno2', 'dl21b'},
    'fssai_number': {'fssai', 'fssaino'},
    'gstin': {'gstno', 'gstin'},
    'phone': {'mob', 'mobile', 'tel', 'phone'},
    'state': {'station', 'state'},
    'party_type': {'grname', 'group', 'acgroup', 'type'},
}


def _map_dbf_fields(field_names: list[str]) -> dict[str, str]:
    """Map DBF 10-char column names to canonical Cenvora fields."""
    mapping = {}
    used = set()
    for raw_col in field_names:
        norm = _norm_header(raw_col)
        if not norm:
            continue
        matched = None
        for canonical, aliases in _DBF_EXTRA_ALIASES.items():
            if norm in aliases or norm == canonical:
                matched = canonical
                break
        if not matched:
            for canonical, aliases in FIELD_ALIASES.items():
                if norm in aliases or norm == canonical:
                    matched = canonical
                    break
        if matched and matched not in used:
            mapping[raw_col] = matched
            used.add(matched)
    return mapping


def _classify_dbf_table(filename: str, mapped_fields: set[str]) -> str:
    """Classify a DBF table as 'products', 'parties', or 'batches'."""
    base = os.path.basename(filename).upper()
    if any(k in base for k in ('LEDGER', 'PARTY', 'ACMAST', 'CUST', 'VEND')):
        return 'parties'
    if any(k in base for k in ('BATCH', 'STOCK')):
        return 'batches'
    if any(k in base for k in ('ITEM', 'PROD', 'PRO')):
        return 'products'
    return detect_marg_sheet_type(mapped_fields)


def parse_marg_dbf_zip(zip_bytes: bytes) -> dict:
    """
    Extract and parse all .DBF files inside a zipped Marg DATA folder.
    Returns {'products': [...], 'parties': [...], 'batches': [...], 'detected_files': [...]}.
    """
    products = []
    parties = []
    batches = []
    detected_files = []
    code_to_name = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            dbf_members = [
                m for m in zf.namelist()
                if m.lower().endswith('.dbf') and not m.startswith('__MACOSX')
            ]

            for member in dbf_members:
                # Zip-slip protection
                safe_name = os.path.basename(member)
                if not safe_name:
                    continue
                target_path = os.path.join(tmpdir, safe_name)
                with zf.open(member) as src, open(target_path, 'wb') as dst:
                    dst.write(src.read())

                table = DBF(
                    target_path,
                    encoding='cp1252',
                    ignore_missing_memofile=True,
                    char_decode_errors='replace',
                )
                col_map = _map_dbf_fields(table.field_names)
                entity_type = _classify_dbf_table(safe_name, set(col_map.values()))
                detected_files.append({'file': safe_name, 'entity_type': entity_type})

                for raw_rec in table:
                    rec = {}
                    for dbf_col, canonical in col_map.items():
                        val = raw_rec.get(dbf_col)
                        if isinstance(val, str):
                            val = val.strip()
                        rec[canonical] = val

                    if entity_type == 'products':
                        if rec.get('sale_price') in (None, '') and rec.get('rate_a') not in (None, ''):
                            rec['sale_price'] = rec['rate_a']
                        if rec.get('item_code') and rec.get('name'):
                            code_to_name[str(rec['item_code']).strip().lower()] = str(rec['name']).strip()
                        products.append(rec)

                    elif entity_type == 'parties':
                        raw_pt = str(rec.get('party_type') or '').lower()
                        if 'creditor' in raw_pt or 'vendor' in raw_pt or 'supplier' in raw_pt:
                            rec['party_type'] = 'vendor'
                        elif 'both' in raw_pt:
                            rec['party_type'] = 'both'
                        else:
                            rec['party_type'] = 'customer'
                        parties.append(rec)

                    elif entity_type == 'batches':
                        if not rec.get('product_name') and rec.get('name'):
                            rec['product_name'] = rec['name']
                        if rec.get('sale_price') in (None, '') and rec.get('rate_a') not in (None, ''):
                            rec['sale_price'] = rec['rate_a']
                        if rec.get('cost_price') in (None, '') and rec.get('price') not in (None, ''):
                            rec['cost_price'] = rec['price']
                        batches.append(rec)

    # Link batch rows missing product_name via item_code from ITEM.DBF
    for b in batches:
        if not b.get('product_name') and b.get('item_code'):
            looked_up = code_to_name.get(str(b['item_code']).strip().lower())
            if looked_up:
                b['product_name'] = looked_up

    return {
        'products': products,
        'parties': parties,
        'batches': batches,
        'detected_files': detected_files,
    }

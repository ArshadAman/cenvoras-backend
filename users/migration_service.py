"""
3-Stage Pre-Flight Validation Gate for TallyPrime & Marg ERP 9+ Migrations.

Stage 1: Regex & Domain Auto-Sanitization
  - Currency/noise stripping (₹, Rs., /-, commas, %)
  - Regulatory last-day snap for MM/YY and MM/YYYY expiry dates
  - Smart Marg packing parser (e.g. '10*10' -> primary='strip', secondary='box', factor=10)
  - GSTIN & 10-digit Indian phone normalization

Stage 2: In-Memory Dry-Run Simulation
  - Zero database writes
  - Classifies rows into Clean, Auto-Fixed Warnings, and Blockers
  - Detects duplicate item_code conflicts and invalid numeric/date values

Stage 3: Atomic Database Commit
  - Executes strictly inside django.db.transaction.atomic()
  - Refuses commit if any blockers exist
  - 100% rollback guarantee on any database integrity error
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)

_GSTIN_REGEX = re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}$')
_PACKING_MULTI_REGEX = re.compile(r'^(\d+)\s*[*xX×]\s*(\d+)\s*([a-zA-Z]*)$')
_PACKING_SINGLE_REGEX = re.compile(r'^(\d+)\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*(ml|gm|g|mg|l|kg|pcs|tab|cap)?$', re.IGNORECASE)


class MigrationBlockerError(Exception):
    """Raised when attempting to commit a migration payload that still has critical blockers."""
    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__(f"Cannot commit migration: {len(blockers)} critical blocker(s) found.")


def sanitize_decimal(value: Any, default: Decimal | None = Decimal('0.00')) -> tuple[Decimal | None, str | None]:
    """
    Strip currency symbols, commas, '/-', '%', 'GST' and parse to Decimal.
    Returns (Decimal_or_None, warning_or_None).
    """
    if value is None or str(value).strip() == '':
        return default, None

    if isinstance(value, Decimal):
        return value, None
    if isinstance(value, (int, float)):
        return Decimal(str(value)), None

    raw = str(value).strip()
    cleaned = raw
    for token in ('₹', 'Rs.', 'Rs', 'INR', '/-', ',', '%', 'gst', 'GST'):
        cleaned = cleaned.replace(token, '')
    cleaned = cleaned.strip()

    try:
        parsed = Decimal(cleaned)
        warning = f"Cleaned '{raw}' -> '{parsed}'" if cleaned != raw else None
        return parsed, warning
    except (InvalidOperation, ValueError):
        match = re.search(r'-?\d+(?:\.\d+)?', cleaned)
        if match:
            parsed = Decimal(match.group(0))
            return parsed, f"Extracted numeric '{parsed}' from '{raw}'"
        return None, f"Unparseable numeric value: '{raw}'"


def sanitize_gstin(value: Any) -> tuple[str, str | None]:
    """Normalize and validate 15-character Indian GSTIN."""
    if not value:
        return '', None
    cleaned = re.sub(r'\s+', '', str(value)).upper()
    if cleaned in ('', 'NA', 'N/A', 'UNREGISTERED', 'NONE', 'NULL'):
        return '', None
    if _GSTIN_REGEX.match(cleaned):
        return cleaned, None
    return '', f"Invalid GSTIN '{value}' cleared to unregistered"


def sanitize_phone(value: Any) -> tuple[str, str | None]:
    """Extract clean 10-digit Indian phone number if possible."""
    if not value:
        return '', None
    digits = re.sub(r'\D', '', str(value))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10:
        return digits, None
    return str(value).strip()[:20], f"Non-standard phone '{value}' kept as raw"


def parse_marg_expiry(value: Any) -> tuple[date | None, str | None, bool]:
    """
    Regulatory Last-Day Snap for pharma expiry dates.
    Parses 'MM/YY' or 'MM/YYYY' (e.g. '11/26') to the last calendar day of that month ('2026-11-30').
    Also supports 'DD/MM/YYYY' and 'YYYY-MM-DD'.
    Returns: (parsed_date_or_None, message_or_None, is_error)
    """
    if value is None or str(value).strip() in ('', '00/00', '00/0000', 'NA', 'N/A', 'None'):
        return None, None, False

    if isinstance(value, datetime):
        return value.date(), None, False
    if isinstance(value, date):
        return value, None, False

    raw = str(value).strip()
    normalized = raw.replace('-', '/').replace('.', '/')
    parts = [p.strip() for p in normalized.split('/') if p.strip()]

    try:
        # Case 1: MM/YY or MM/YYYY
        if len(parts) == 2:
            month = int(parts[0])
            year = int(parts[1])
            if year < 100:
                year += 2000
            if not (1 <= month <= 12):
                return None, f"Invalid month in expiry '{raw}'", True
            last_day = calendar.monthrange(year, month)[1]
            snapped = date(year, month, last_day)
            return snapped, f"Snapped expiry '{raw}' -> {snapped.isoformat()}", False

        # Case 2: YYYY/MM/DD or DD/MM/YYYY
        if len(parts) == 3:
            if len(parts[0]) == 4:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            else:
                d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 100:
                    y += 2000
            return date(y, m, d), None, False
    except (ValueError, TypeError):
        pass

    return None, f"Unrecognized expiry date format: '{raw}'", True


def parse_marg_packing(value: Any, default_unit: str = 'pcs') -> dict:
    """
    Parse Marg ERP packing strings (e.g. '10*10', '1x10', '1*100ml', '24*1').
    Returns dict with keys: packing, unit, secondary_unit, conversion_factor.
    """
    if not value or not str(value).strip():
        return {
            'packing': '',
            'unit': default_unit,
            'secondary_unit': '',
            'conversion_factor': 1,
        }

    raw = str(value).strip()
    display = raw.replace('*', 'x').replace('×', 'x')

    m_multi = _PACKING_MULTI_REGEX.match(raw)
    if m_multi:
        first_num = int(m_multi.group(1))
        second_num = int(m_multi.group(2))
        suffix = (m_multi.group(3) or '').lower()

        if suffix in ('ml', 'gm', 'g', 'mg', 'l', 'kg'):
            # e.g. '1*100ml' -> 1 bottle of 100ml
            return {
                'packing': display,
                'unit': 'bottle' if suffix in ('ml', 'l') else 'pack',
                'secondary_unit': 'box' if first_num > 1 else '',
                'conversion_factor': first_num if first_num > 1 else 1,
            }

        # Standard blister/strip packing e.g. '10*10' -> 1 Box = 10 Strips (each 10 tabs)
        return {
            'packing': display,
            'unit': 'strip',
            'secondary_unit': 'box',
            'conversion_factor': max(first_num, 1) if first_num > 1 else max(second_num, 1),
        }

    return {
        'packing': display,
        'unit': default_unit,
        'secondary_unit': '',
        'conversion_factor': 1,
    }


@dataclass
class DryRunReport:
    can_commit: bool
    total_rows: int = 0
    clean_count: int = 0
    auto_fixed_count: int = 0
    blocker_count: int = 0
    blockers: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    sanitized_payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'can_commit': self.can_commit,
            'summary': {
                'total_rows': self.total_rows,
                'clean_count': self.clean_count,
                'auto_fixed_count': self.auto_fixed_count,
                'blocker_count': self.blocker_count,
            },
            'blockers': self.blockers,
            'warnings': self.warnings,
        }


class MigrationValidationGate:
    """
    3-Stage Pre-Flight Validation Gate for Tally & Marg ERP imports.
    """

    def __init__(self, tenant, clamp_negative_stock: bool = True):
        self.tenant = tenant
        self.clamp_negative_stock = clamp_negative_stock

    def dry_run(
        self,
        products: list[dict] | None = None,
        parties: list[dict] | None = None,
        batches: list[dict] | None = None,
    ) -> DryRunReport:
        """
        Stage 1 + Stage 2: Sanitize and validate all rows in memory with zero DB writes.
        """
        products = products or []
        parties = parties or []
        batches = batches or []

        blockers = []
        warnings = []
        clean_rows = 0
        auto_fixed_rows = 0

        sanitized_products = []
        sanitized_parties = []
        sanitized_batches = []

        seen_item_codes = {}  # item_code -> tax_rate

        # --- 1. Validate & Sanitize Products ---
        for idx, row in enumerate(products, start=1):
            row_fixed = False
            row_blocked = False
            name = str(row.get('name') or '').strip()
            if not name:
                blockers.append({'entity': 'product', 'row': idx, 'field': 'name', 'message': 'Product name is required', 'raw_value': row.get('name')})
                continue

            item_code = str(row.get('item_code') or '').strip()
            tax_val, tax_warn = sanitize_decimal(row.get('tax', 0), default=Decimal('0.00'))
            if tax_val is None:
                blockers.append({'entity': 'product', 'row': idx, 'field': 'tax', 'message': tax_warn, 'raw_value': row.get('tax')})
                row_blocked = True
            elif tax_warn:
                warnings.append({'entity': 'product', 'row': idx, 'field': 'tax', 'message': tax_warn})
                row_fixed = True

            # Check duplicate item_code tax conflict
            if item_code and tax_val is not None:
                if item_code in seen_item_codes and seen_item_codes[item_code] != tax_val:
                    blockers.append({
                        'entity': 'product',
                        'row': idx,
                        'field': 'item_code',
                        'message': f"Duplicate item_code '{item_code}' has conflicting GST% ({seen_item_codes[item_code]}% vs {tax_val}%)",
                        'raw_value': item_code,
                    })
                    row_blocked = True
                else:
                    seen_item_codes[item_code] = tax_val

            price_val, price_warn = sanitize_decimal(row.get('price', 0), default=Decimal('0.00'))
            sale_price_val, sp_warn = sanitize_decimal(row.get('sale_price'), default=None)
            if price_val is None or price_val < 0:
                blockers.append({'entity': 'product', 'row': idx, 'field': 'price', 'message': 'Invalid or negative purchase price', 'raw_value': row.get('price')})
                row_blocked = True
            elif price_warn:
                warnings.append({'entity': 'product', 'row': idx, 'field': 'price', 'message': price_warn})
                row_fixed = True

            if sp_warn and sale_price_val is None:
                blockers.append({'entity': 'product', 'row': idx, 'field': 'sale_price', 'message': sp_warn, 'raw_value': row.get('sale_price')})
                row_blocked = True
            elif sp_warn:
                warnings.append({'entity': 'product', 'row': idx, 'field': 'sale_price', 'message': sp_warn})
                row_fixed = True

            packing_info = parse_marg_packing(row.get('packing'), default_unit=str(row.get('unit') or 'pcs').strip() or 'pcs')

            if row_blocked:
                continue

            if row_fixed:
                auto_fixed_rows += 1
            else:
                clean_rows += 1

            sanitized_products.append({
                'name': name,
                'item_code': item_code or None,
                'hsn_sac_code': str(row.get('hsn_sac_code') or '').strip(),
                'manufacturer': str(row.get('manufacturer') or '').strip(),
                'unit': packing_info['unit'],
                'secondary_unit': row.get('secondary_unit') or packing_info['secondary_unit'] or None,
                'conversion_factor': int(row.get('conversion_factor') or packing_info['conversion_factor'] or 1),
                'packing': packing_info['packing'],
                'price': price_val,
                'sale_price': sale_price_val,
                'tax': tax_val,
            })

        # --- 2. Validate & Sanitize Parties (Customers & Vendors) ---
        for idx, row in enumerate(parties, start=1):
            row_fixed = False
            name = str(row.get('name') or '').strip()
            if not name:
                blockers.append({'entity': 'party', 'row': idx, 'field': 'name', 'message': 'Party name is required', 'raw_value': row.get('name')})
                continue

            party_type = str(row.get('party_type') or 'customer').lower().strip()
            if party_type not in ('customer', 'vendor', 'both'):
                party_type = 'customer'

            gstin, gstin_warn = sanitize_gstin(row.get('gstin'))
            if gstin_warn:
                warnings.append({'entity': 'party', 'row': idx, 'field': 'gstin', 'message': gstin_warn})
                row_fixed = True

            phone, phone_warn = sanitize_phone(row.get('phone'))
            if phone_warn:
                warnings.append({'entity': 'party', 'row': idx, 'field': 'phone', 'message': phone_warn})
                row_fixed = True

            ob_val, ob_warn = sanitize_decimal(row.get('opening_balance', 0), default=Decimal('0.00'))
            if ob_val is None:
                blockers.append({'entity': 'party', 'row': idx, 'field': 'opening_balance', 'message': ob_warn, 'raw_value': row.get('opening_balance')})
                continue
            elif ob_warn:
                warnings.append({'entity': 'party', 'row': idx, 'field': 'opening_balance', 'message': ob_warn})
                row_fixed = True

            if row_fixed:
                auto_fixed_rows += 1
            else:
                clean_rows += 1

            raw_tier = str(row.get('default_price_tier') or 'rate_a').lower().strip()
            if 'b' == raw_tier or 'rate_b' in raw_tier or 'wholesale' in raw_tier:
                price_tier = 'rate_b'
            elif 'c' == raw_tier or 'rate_c' in raw_tier or 'hospital' in raw_tier or 'special' in raw_tier:
                price_tier = 'rate_c'
            else:
                price_tier = 'rate_a'

            dl_exp, _, _ = parse_marg_expiry(row.get('dl_expiry_date'))

            sanitized_parties.append({
                'name': name,
                'party_type': party_type,
                'gstin': gstin,
                'phone': phone,
                'email': str(row.get('email') or '').strip() or None,
                'address': str(row.get('address') or '').strip(),
                'state': str(row.get('state') or '').strip(),
                'opening_balance': ob_val,
                'dl_number_20b': str(row.get('dl_number_20b') or '').strip(),
                'dl_number_21b': str(row.get('dl_number_21b') or '').strip(),
                'dl_expiry_date': dl_exp,
                'fssai_number': str(row.get('fssai_number') or '').strip(),
                'default_price_tier': price_tier,
            })

        # --- 3. Validate & Sanitize Opening Stock Batches ---
        for idx, row in enumerate(batches, start=1):
            row_fixed = False
            row_blocked = False
            prod_name = str(row.get('product_name') or '').strip()
            batch_no = str(row.get('batch_number') or 'DEFAULT').strip() or 'DEFAULT'
            if not prod_name:
                blockers.append({'entity': 'batch', 'row': idx, 'field': 'product_name', 'message': 'Product name is required for batch stock', 'raw_value': row.get('product_name')})
                continue

            exp_date, exp_msg, exp_err = parse_marg_expiry(row.get('expiry_date'))
            if exp_err:
                blockers.append({'entity': 'batch', 'row': idx, 'field': 'expiry_date', 'message': exp_msg, 'raw_value': row.get('expiry_date')})
                row_blocked = True
            elif exp_msg:
                warnings.append({'entity': 'batch', 'row': idx, 'field': 'expiry_date', 'message': exp_msg})
                row_fixed = True

            qty_val, qty_warn = sanitize_decimal(row.get('quantity', 0), default=Decimal('0.000'))
            if qty_val is None:
                blockers.append({'entity': 'batch', 'row': idx, 'field': 'quantity', 'message': qty_warn, 'raw_value': row.get('quantity')})
                row_blocked = True
            elif qty_val < 0:
                if self.clamp_negative_stock:
                    warnings.append({'entity': 'batch', 'row': idx, 'field': 'quantity', 'message': f"Negative stock {qty_val} clamped to 0"})
                    qty_val = Decimal('0.000')
                    row_fixed = True
                else:
                    blockers.append({'entity': 'batch', 'row': idx, 'field': 'quantity', 'message': 'Negative opening stock not allowed', 'raw_value': str(qty_val)})
                    row_blocked = True
            elif qty_warn:
                warnings.append({'entity': 'batch', 'row': idx, 'field': 'quantity', 'message': qty_warn})
                row_fixed = True

            mrp_val, _ = sanitize_decimal(row.get('mrp', 0), default=Decimal('0.00'))
            cost_val, _ = sanitize_decimal(row.get('cost_price', 0), default=Decimal('0.00'))
            sale_val, _ = sanitize_decimal(row.get('sale_price', 0), default=Decimal('0.00'))
            rate_a_val, _ = sanitize_decimal(row.get('rate_a', sale_val or 0), default=Decimal('0.00'))
            rate_b_val, _ = sanitize_decimal(row.get('rate_b', 0), default=Decimal('0.00'))
            rate_c_val, _ = sanitize_decimal(row.get('rate_c', 0), default=Decimal('0.00'))

            if row_blocked:
                continue

            if row_fixed:
                auto_fixed_rows += 1
            else:
                clean_rows += 1

            sanitized_batches.append({
                'product_name': prod_name,
                'item_code': str(row.get('item_code') or '').strip() or None,
                'batch_number': batch_no,
                'expiry_date': exp_date,
                'warehouse_name': str(row.get('warehouse_name') or 'Main Godown').strip() or 'Main Godown',
                'quantity': qty_val,
                'mrp': mrp_val if mrp_val is not None else Decimal('0.00'),
                'cost_price': cost_val if cost_val is not None else Decimal('0.00'),
                'sale_price': sale_val if sale_val is not None else Decimal('0.00'),
                'rate_a': rate_a_val if rate_a_val is not None else Decimal('0.00'),
                'rate_b': rate_b_val if rate_b_val is not None else Decimal('0.00'),
                'rate_c': rate_c_val if rate_c_val is not None else Decimal('0.00'),
            })

        total_rows = len(products) + len(parties) + len(batches)
        return DryRunReport(
            can_commit=(len(blockers) == 0),
            total_rows=total_rows,
            clean_count=clean_rows,
            auto_fixed_count=auto_fixed_rows,
            blocker_count=len(blockers),
            blockers=blockers,
            warnings=warnings,
            sanitized_payload={
                'products': sanitized_products,
                'parties': sanitized_parties,
                'batches': sanitized_batches,
            },
        )

    def commit(
        self,
        products: list[dict] | None = None,
        parties: list[dict] | None = None,
        batches: list[dict] | None = None,
        dry_run_report: DryRunReport | None = None,
        historical_vouchers: list[dict] | None = None,
        post_gl_opening_balances: bool = True,
        cutover_date: date | None = None,
    ) -> dict:
        """
        Stage 3: Atomically commit validated payload to PostgreSQL inside transaction.atomic().
        Refuses to commit if blockers exist.
        Also posts double-entry GL opening balances offset against Account 3999 (Task 2.6)
        and archives historical vouchers into HistoricalDocumentArchive (Task 2.5).
        """
        from billing.models import Customer, Vendor
        from billing.models_archive import HistoricalDocumentArchive
        from inventory.models import Product, ProductBatch, StockPoint, Warehouse
        from ledger.services import AccountingService

        report = dry_run_report or self.dry_run(products=products, parties=parties, batches=batches)
        if not report.can_commit:
            raise MigrationBlockerError(report.blockers)

        payload = report.sanitized_payload
        created_counts = {
            'products': 0,
            'customers': 0,
            'vendors': 0,
            'batches': 0,
            'stock_points': 0,
            'historical_archives': 0,
            'gl_entries': 0,
        }

        customer_gl_balances = []
        vendor_gl_balances = []
        total_stock_valuation = Decimal('0.00')

        with transaction.atomic():
            product_map = {}

            # 1. Upsert Products
            for p_data in payload.get('products', []):
                lookup = {'created_by': self.tenant}
                if p_data['item_code']:
                    lookup['item_code'] = p_data['item_code']
                else:
                    lookup['name'] = p_data['name']

                prod, created = Product.objects.update_or_create(
                    **lookup,
                    defaults={
                        'name': p_data['name'],
                        'hsn_sac_code': p_data['hsn_sac_code'],
                        'manufacturer': p_data['manufacturer'],
                        'unit': p_data['unit'],
                        'secondary_unit': p_data['secondary_unit'],
                        'conversion_factor': p_data['conversion_factor'],
                        'price': p_data['price'],
                        'sale_price': p_data['sale_price'],
                        'tax': p_data['tax'],
                    },
                )
                product_map[p_data['name'].lower()] = prod
                if p_data['item_code']:
                    product_map[p_data['item_code'].lower()] = prod
                if created:
                    created_counts['products'] += 1

            # 2. Upsert Parties (Customers / Vendors)
            for party in payload.get('parties', []):
                if party['party_type'] in ('customer', 'both'):
                    cust_obj, created = Customer.objects.update_or_create(
                        created_by=self.tenant,
                        name=party['name'],
                        defaults={
                            'gstin': party['gstin'],
                            'phone': party['phone'],
                            'email': party['email'],
                            'address': party['address'],
                            'state': party['state'],
                            'current_balance': party['opening_balance'],
                            'dl_number_20b': party.get('dl_number_20b', ''),
                            'dl_number_21b': party.get('dl_number_21b', ''),
                            'dl_expiry_date': party.get('dl_expiry_date'),
                            'fssai_number': party.get('fssai_number', ''),
                            'default_price_tier': party.get('default_price_tier', 'rate_a'),
                        },
                    )
                    if party['opening_balance'] != Decimal('0.00'):
                        customer_gl_balances.append((cust_obj, party['opening_balance']))
                    if created:
                        created_counts['customers'] += 1

                if party['party_type'] in ('vendor', 'both'):
                    vend_obj, created = Vendor.objects.update_or_create(
                        created_by=self.tenant,
                        name=party['name'],
                        defaults={
                            'gstin': party['gstin'],
                            'phone': party['phone'],
                            'email': party['email'],
                            'address': party['address'],
                            'state': party['state'],
                            'dl_number_20b': party.get('dl_number_20b', ''),
                            'dl_number_21b': party.get('dl_number_21b', ''),
                            'fssai_number': party.get('fssai_number', ''),
                        },
                    )
                    if party['party_type'] == 'vendor' and party['opening_balance'] != Decimal('0.00'):
                        vendor_gl_balances.append((vend_obj, party['opening_balance']))
                    if created:
                        created_counts['vendors'] += 1

            # 3. Upsert Batches & StockPoints
            warehouse_cache = {}
            for b_data in payload.get('batches', []):
                wh_name = b_data['warehouse_name']
                if wh_name not in warehouse_cache:
                    wh, _ = Warehouse.objects.get_or_create(
                        created_by=self.tenant,
                        name=wh_name,
                    )
                    warehouse_cache[wh_name] = wh
                wh = warehouse_cache[wh_name]

                prod = None
                if b_data['item_code'] and b_data['item_code'].lower() in product_map:
                    prod = product_map[b_data['item_code'].lower()]
                elif b_data['product_name'].lower() in product_map:
                    prod = product_map[b_data['product_name'].lower()]
                else:
                    prod = Product.objects.filter(
                        created_by=self.tenant,
                        name__iexact=b_data['product_name'],
                    ).first()
                    if not prod:
                        prod = Product.objects.create(
                            created_by=self.tenant,
                            name=b_data['product_name'],
                            item_code=b_data['item_code'],
                        )
                        product_map[b_data['product_name'].lower()] = prod

                batch, b_created = ProductBatch.objects.update_or_create(
                    product=prod,
                    batch_number=b_data['batch_number'],
                    defaults={
                        'expiry_date': b_data['expiry_date'],
                        'mrp': b_data['mrp'],
                        'cost_price': b_data['cost_price'],
                        'sale_price': b_data['sale_price'],
                        'rate_a': b_data.get('rate_a', Decimal('0.00')),
                        'rate_b': b_data.get('rate_b', Decimal('0.00')),
                        'rate_c': b_data.get('rate_c', Decimal('0.00')),
                    },
                )
                if b_created:
                    created_counts['batches'] += 1

                sp, sp_created = StockPoint.objects.update_or_create(
                    warehouse=wh,
                    batch=batch,
                    defaults={'quantity': b_data['quantity']},
                )
                if sp_created:
                    created_counts['stock_points'] += 1
                total_stock_valuation += (b_data['quantity'] * (b_data['cost_price'] or Decimal('0.00')))
                prod.recalculate_stock(save=True)

            # 4. Task 2.6: Post Opening Balance GL entries balanced against Account 3999
            if post_gl_opening_balances and (customer_gl_balances or vendor_gl_balances or total_stock_valuation > 0):
                created_counts['gl_entries'] = AccountingService.post_migration_opening_balances(
                    tenant=self.tenant,
                    cutover_date=cutover_date,
                    customer_balances=customer_gl_balances,
                    vendor_balances=vendor_gl_balances,
                    opening_stock_value=total_stock_valuation,
                )

            # 5. Task 2.5: Persist Historical Document Archive
            for hv in (historical_vouchers or []):
                doc_no = str(hv.get('document_number') or '').strip()
                if not doc_no:
                    continue
                doc_dt = None
                if hv.get('document_date'):
                    doc_dt, _, _ = parse_marg_expiry(hv.get('document_date'))
                _, h_created = HistoricalDocumentArchive.objects.update_or_create(
                    tenant=self.tenant,
                    source_erp=hv.get('source', 'tally'),
                    voucher_type=hv.get('voucher_type', 'Sales'),
                    document_number=doc_no,
                    defaults={
                        'document_date': doc_dt,
                        'party_name': str(hv.get('party_name') or '')[:255],
                        'party_gstin': str(hv.get('party_gstin') or '')[:20],
                        'total_amount': Decimal(str(hv.get('total_amount') or 0)),
                        'items': hv.get('items', []),
                        'raw_metadata': hv.get('raw_metadata', {}),
                    },
                )
                if h_created:
                    created_counts['historical_archives'] += 1

        return {
            'success': True,
            'created': created_counts,
            'warnings': report.warnings,
        }

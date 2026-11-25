"""
1-Click Complete Business Backup ZIP Service (users/backup_service.py).
Provides full Data Sovereignty & Zero Lock-In for Cenvora tenants by exporting
every tenant record into JSON, human-readable CSVs, Tally XML, and a SHA-256
verified manifest.json inside a single timestamped ZIP archive.
"""
import csv
from datetime import datetime
import hashlib
import io
import json
import zipfile

from django.core.serializers.json import DjangoJSONEncoder

from billing.models import (
    Customer,
    Payment,
    PurchaseBill,
    SalesInvoice,
    Vendor,
)
from billing.models_archive import HistoricalDocumentArchive
from billing.models_returns import CreditNote, DebitNote
from billing.tally_exporter import TallyXMLExporter
from inventory.models import Product, ProductBatch, StockPoint, Warehouse
from ledger.models import Account, GeneralLedgerEntry


def _csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def generate_complete_business_backup_zip(user) -> tuple[bytes, str]:
    """
    Generate a complete, tenant-isolated backup ZIP containing:
      - manifest.json (metadata, counts, SHA-256 checksums)
      - json/full_backup.json
      - csv/*.csv (products, batches_stock, customers, vendors, sales_invoices, purchase_bills, general_ledger)
      - tally/Tally_Full_Backup_Inventory.xml
    """
    tenant = getattr(user, 'active_tenant', user)
    biz_name = getattr(tenant, 'business_name', '') or getattr(tenant, 'username', 'Company')
    safe_name = "".join(c for c in biz_name if c.isalnum() or c in ('-', '_')).strip() or 'Company'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{safe_name}_Complete_Backup_{stamp}.zip"

    # 1. Query all tenant-scoped data
    products = list(Product.objects.filter(created_by=tenant).order_by('name'))
    warehouses = list(Warehouse.objects.filter(created_by=tenant).order_by('name'))
    batches = list(ProductBatch.objects.filter(product__created_by=tenant).select_related('product').order_by('product__name', 'batch_number'))
    stock_points = list(StockPoint.objects.filter(warehouse__created_by=tenant).select_related('warehouse', 'batch', 'batch__product'))
    customers = list(Customer.objects.filter(created_by=tenant).order_by('name'))
    vendors = list(Vendor.objects.filter(created_by=tenant).order_by('name'))
    sales = list(SalesInvoice.objects.filter(created_by=tenant).prefetch_related('items__product').order_by('-invoice_date'))
    purchases = list(PurchaseBill.objects.filter(created_by=tenant).prefetch_related('items__product').order_by('-bill_date'))
    credit_notes = list(CreditNote.objects.filter(created_by=tenant).order_by('-date'))
    debit_notes = list(DebitNote.objects.filter(created_by=tenant).order_by('-date'))
    payments = list(Payment.objects.filter(created_by=tenant).select_related('customer').order_by('-date'))
    accounts = list(Account.objects.filter(created_by=tenant).order_by('code', 'name'))
    gl_entries = list(GeneralLedgerEntry.objects.filter(created_by=tenant).select_related('account', 'customer', 'vendor').order_by('-date', '-created_at'))
    archives = list(HistoricalDocumentArchive.objects.filter(tenant=tenant).order_by('-document_date'))

    # 2. Build structured JSON payload
    json_payload = {
        'business_profile': {
            'id': str(tenant.id),
            'business_name': biz_name,
            'gstin': getattr(tenant, 'gstin', '') or '',
            'pan_number': getattr(tenant, 'pan_number', '') or '',
            'dl_number': getattr(tenant, 'dl_number', '') or '',
            'state': getattr(tenant, 'state', '') or '',
            'exported_at': datetime.now().isoformat(),
        },
        'warehouses': [
            {'id': str(w.id), 'name': w.name, 'address': w.address, 'gstin': w.gstin, 'is_active': w.is_active}
            for w in warehouses
        ],
        'products': [
            {
                'id': str(p.id), 'item_code': p.item_code, 'name': p.name, 'hsn_sac_code': p.hsn_sac_code,
                'manufacturer': p.manufacturer, 'unit': p.unit, 'secondary_unit': p.secondary_unit,
                'conversion_factor': p.conversion_factor, 'price': p.price, 'sale_price': p.sale_price,
                'tax': p.tax, 'stock': p.stock, 'is_active': p.is_active,
            }
            for p in products
        ],
        'batches': [
            {
                'id': str(b.id), 'product_id': str(b.product_id), 'product_name': b.product.name,
                'batch_number': b.batch_number, 'expiry_date': b.expiry_date,
                'manufacturing_date': b.manufacturing_date, 'mrp': b.mrp, 'cost_price': b.cost_price,
                'sale_price': b.sale_price, 'rate_a': b.rate_a, 'rate_b': b.rate_b, 'rate_c': b.rate_c,
            }
            for b in batches
        ],
        'stock_points': [
            {
                'warehouse': sp.warehouse.name, 'product': sp.batch.product.name,
                'batch_number': sp.batch.batch_number, 'quantity': sp.quantity,
            }
            for sp in stock_points
        ],
        'customers': [
            {
                'id': str(c.id), 'name': c.name, 'gstin': c.gstin, 'phone': c.phone, 'email': c.email,
                'address': c.address, 'state': c.state, 'current_balance': c.current_balance,
                'credit_limit': c.credit_limit, 'default_price_tier': c.default_price_tier,
                'dl_number_20b': c.dl_number_20b, 'dl_number_21b': c.dl_number_21b,
                'dl_expiry_date': c.dl_expiry_date, 'fssai_number': c.fssai_number,
            }
            for c in customers
        ],
        'vendors': [
            {
                'id': str(v.id), 'name': v.name, 'gstin': v.gstin, 'phone': v.phone, 'email': v.email,
                'address': v.address, 'state': v.state, 'dl_number_20b': v.dl_number_20b,
                'dl_number_21b': v.dl_number_21b, 'fssai_number': v.fssai_number,
            }
            for v in vendors
        ],
        'sales_invoices': [
            {
                'id': str(inv.id), 'invoice_number': inv.invoice_number, 'invoice_date': inv.invoice_date,
                'customer_name': inv.customer_name, 'status': inv.status, 'place_of_supply': inv.place_of_supply,
                'total_amount': inv.total_amount, 'amount_paid': inv.amount_paid, 'payment_status': inv.payment_status,
                'items': [
                    {
                        'product': itm.product.name if itm.product else '',
                        'quantity': itm.quantity, 'free_quantity': itm.free_quantity,
                        'price': itm.price, 'discount': itm.discount, 'tax': itm.tax, 'amount': itm.amount,
                    }
                    for itm in inv.items.all()
                ],
            }
            for inv in sales
        ],
        'purchase_bills': [
            {
                'id': str(b.id), 'bill_number': b.bill_number, 'bill_date': b.bill_date,
                'vendor_name': b.vendor_name, 'vendor_gstin': b.vendor_gstin,
                'total_amount': b.total_amount, 'amount_paid': b.amount_paid, 'payment_status': b.payment_status,
                'items': [
                    {
                        'product': itm.product.name if itm.product else '',
                        'quantity': itm.quantity, 'free_quantity': itm.free_quantity,
                        'price': itm.price, 'discount': itm.discount, 'tax': itm.tax, 'amount': itm.amount,
                    }
                    for itm in b.items.all()
                ],
            }
            for b in purchases
        ],
        'credit_notes': [
            {'id': str(cn.id), 'number': cn.credit_note_number, 'date': cn.date, 'total_amount': cn.total_amount}
            for cn in credit_notes
        ],
        'debit_notes': [
            {'id': str(dn.id), 'number': dn.debit_note_number, 'date': dn.date, 'total_amount': dn.total_amount}
            for dn in debit_notes
        ],
        'payments': [
            {
                'id': str(pm.id), 'customer': pm.customer.name if pm.customer else '',
                'date': pm.date, 'amount': pm.amount, 'mode': pm.mode, 'reference': pm.reference,
            }
            for pm in payments
        ],
        'accounts': [
            {'code': a.code, 'name': a.name, 'account_type': a.account_type}
            for a in accounts
        ],
        'general_ledger_entries': [
            {
                'id': str(gl.id), 'date': gl.date, 'account_code': gl.account.code, 'account_name': gl.account.name,
                'debit': gl.debit, 'credit': gl.credit, 'reference': gl.reference, 'description': gl.description,
            }
            for gl in gl_entries
        ],
        'historical_archives': [
            {
                'source_erp': ar.source_erp, 'voucher_type': ar.voucher_type,
                'document_number': ar.document_number, 'document_date': ar.document_date,
                'party_name': ar.party_name, 'total_amount': ar.total_amount, 'items': ar.items,
            }
            for ar in archives
        ],
    }

    full_json_bytes = json.dumps(json_payload, cls=DjangoJSONEncoder, indent=2).encode('utf-8')

    # 3. Build CSV files
    csv_files = {
        'csv/products.csv': _csv_bytes(
            ['Item Code', 'Name', 'HSN', 'Manufacturer', 'Unit', 'Secondary Unit', 'Conv Factor', 'Cost Price', 'Sale Price', 'GST %', 'Stock'],
            [[p.item_code or '', p.name, p.hsn_sac_code or '', p.manufacturer or '', p.unit, p.secondary_unit or '', p.conversion_factor, p.price, p.sale_price or '', p.tax, p.stock] for p in products],
        ),
        'csv/batches_stock.csv': _csv_bytes(
            ['Warehouse', 'Product', 'Batch Number', 'Expiry Date', 'Quantity', 'MRP', 'Cost Price', 'Rate A', 'Rate B', 'Rate C'],
            [[sp.warehouse.name, sp.batch.product.name, sp.batch.batch_number, sp.batch.expiry_date or '', sp.quantity, sp.batch.mrp, sp.batch.cost_price, sp.batch.rate_a, sp.batch.rate_b, sp.batch.rate_c] for sp in stock_points],
        ),
        'csv/customers.csv': _csv_bytes(
            ['Name', 'GSTIN', 'Phone', 'Email', 'State', 'Balance', 'Price Tier', 'DL 20B', 'DL 21B', 'FSSAI'],
            [[c.name, c.gstin or '', c.phone or '', c.email or '', c.state or '', c.current_balance, c.default_price_tier, c.dl_number_20b, c.dl_number_21b, c.fssai_number] for c in customers],
        ),
        'csv/vendors.csv': _csv_bytes(
            ['Name', 'GSTIN', 'Phone', 'Email', 'State', 'DL 20B', 'DL 21B', 'FSSAI'],
            [[v.name, v.gstin or '', v.phone or '', v.email or '', v.state or '', v.dl_number_20b, v.dl_number_21b, v.fssai_number] for v in vendors],
        ),
        'csv/sales_invoices.csv': _csv_bytes(
            ['Invoice No', 'Date', 'Customer', 'Status', 'Place of Supply', 'Total Amount', 'Amount Paid', 'Payment Status'],
            [[inv.invoice_number, inv.invoice_date, inv.customer_name or '', inv.status, inv.place_of_supply or '', inv.total_amount, inv.amount_paid, inv.payment_status] for inv in sales],
        ),
        'csv/purchase_bills.csv': _csv_bytes(
            ['Bill No', 'Date', 'Vendor', 'Vendor GSTIN', 'Total Amount', 'Amount Paid', 'Payment Status'],
            [[b.bill_number, b.bill_date, b.vendor_name, b.vendor_gstin or '', b.total_amount, b.amount_paid, b.payment_status] for b in purchases],
        ),
        'csv/general_ledger.csv': _csv_bytes(
            ['Date', 'Account Code', 'Account Name', 'Debit', 'Credit', 'Reference', 'Description'],
            [[gl.date, gl.account.code, gl.account.name, gl.debit, gl.credit, gl.reference, gl.description] for gl in gl_entries],
        ),
    }

    # 4. Full Tally XML Backup
    tally_xml_bytes = TallyXMLExporter(tenant=tenant, mode='full_inventory').generate_xml().encode('utf-8')

    # 5. Manifest with SHA-256 checksums
    checksums = {'json/full_backup.json': hashlib.sha256(full_json_bytes).hexdigest()}
    for path, content in csv_files.items():
        checksums[path] = hashlib.sha256(content).hexdigest()
    checksums['tally/Tally_Full_Backup_Inventory.xml'] = hashlib.sha256(tally_xml_bytes).hexdigest()

    manifest = {
        'business_name': biz_name,
        'exported_at': datetime.now().isoformat(),
        'counts': {
            'products': len(products),
            'batches': len(batches),
            'customers': len(customers),
            'vendors': len(vendors),
            'sales_invoices': len(sales),
            'purchase_bills': len(purchases),
            'general_ledger_entries': len(gl_entries),
            'historical_archives': len(archives),
        },
        'sha256_checksums': checksums,
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode('utf-8')

    # 6. Assemble ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', manifest_bytes)
        zf.writestr('json/full_backup.json', full_json_bytes)
        for path, content in csv_files.items():
            zf.writestr(path, content)
        zf.writestr('tally/Tally_Full_Backup_Inventory.xml', tally_xml_bytes)

    return zip_buf.getvalue(), filename

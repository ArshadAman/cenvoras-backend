"""
TallyPrime XML Voucher & Master Export Engine.
Generates 100% compliant Tally XML envelopes (<ENVELOPE>) supporting:
- Accounting-Only and Full-Inventory modes
- Auto-creation of party and tax ledgers in <REPORTNAME>All Masters</REPORTNAME>
- Sales Invoices, Purchase Bills, Credit Notes, and Debit Notes
- Inter-state (IGST) vs Intra-state (CGST+SGST) ledger routing
- Free schemes via Tally native <ACTUALQTY> and <BILLEDQTY>
- Configurable CA Chart of Accounts mapping
"""
from decimal import Decimal
import logging
from xml.sax.saxutils import escape
from django.db.models import Q
from billing.gst_utils import (
    get_seller_state_code,
    get_pos_state_code,
    calculate_item_tax_breakup,
)

logger = logging.getLogger(__name__)

DEFAULT_CA_MAPPING = {
    'sales_account': 'Sales Account',
    'interstate_sales_account': 'Interstate Sales Account',
    'purchase_account': 'Purchase Account',
    'interstate_purchase_account': 'Interstate Purchase Account',
    'sales_return_account': 'Sales Returns',
    'purchase_return_account': 'Purchase Returns',
    'cgst_output_prefix': 'Output CGST',
    'sgst_output_prefix': 'Output SGST',
    'igst_output_prefix': 'Output IGST',
    'cgst_input_prefix': 'Input CGST',
    'sgst_input_prefix': 'Input SGST',
    'igst_input_prefix': 'Input IGST',
    'round_off_account': 'Round Off',
    'split_tax_by_rate': True,
    'separate_interstate_sales': False,
    'separate_interstate_purchases': False,
}


def _xml_escape(val) -> str:
    """Escape XML special characters safely."""
    if val is None:
        return ""
    return escape(str(val).strip())


def _format_tally_date(dt) -> str:
    """Format Python date/datetime to Tally's YYYYMMDD format."""
    if not dt:
        return ""
    if hasattr(dt, 'strftime'):
        return dt.strftime('%Y%m%d')
    # string fallback
    clean = str(dt).replace('-', '').replace('/', '')[:8]
    return clean


def _fmt_qty(qty, unit: str = "pcs") -> str:
    """Format quantity string with unit for Tally e.g. ' 10 pcs'."""
    q_val = Decimal(str(qty or 0))
    # format without trailing zeroes if integer
    if q_val == q_val.to_integral():
        q_str = f"{int(q_val)}"
    else:
        q_str = f"{q_val:.3f}".rstrip('0').rstrip('.')
    u_str = (unit or "pcs").strip()
    return f" {q_str} {u_str}"


class TallyXMLExporter:
    """
    High-performance exporter for generating TallyPrime XML packages.
    """

    def __init__(
        self,
        tenant,
        start_date=None,
        end_date=None,
        mode='accounting_only',
        ca_mapping=None,
        include_sales=True,
        include_purchases=True,
        include_returns=True,
    ):
        """
        :param tenant: User instance (the tenant/business owner)
        :param start_date: datetime.date or str YYYY-MM-DD
        :param end_date: datetime.date or str YYYY-MM-DD
        :param mode: 'accounting_only' | 'full_inventory'
        :param ca_mapping: dict overriding default ledger names
        """
        self.tenant = tenant
        self.start_date = start_date
        self.end_date = end_date
        self.mode = mode
        self.include_sales = include_sales
        self.include_purchases = include_purchases
        self.include_returns = include_returns

        self.mapping = dict(DEFAULT_CA_MAPPING)
        if ca_mapping:
            self.mapping.update(ca_mapping)

        self.seller_state_code = get_seller_state_code(tenant)
        self.referenced_masters = {
            'sundry_debtors': {},   # name -> dict(gstin, state, address)
            'sundry_creditors': {}, # name -> dict(gstin, state, address)
            'duties_and_taxes': set(), # set of ledger names
            'sales_ledgers': set(),
            'purchase_ledgers': set(),
            'stock_items': {},      # name -> dict(unit, hsn, gst_rate)
            'godowns': set(),
        }

    def _resolve_output_tax_ledger(self, tax_type: str, rate: Decimal) -> str:
        """Resolve CGST/SGST/IGST output ledger name with CA mapping."""
        half_rate = (rate / Decimal('2')).normalize() if tax_type in ('cgst', 'sgst') else rate.normalize()
        split = self.mapping.get('split_tax_by_rate', True)
        if tax_type == 'cgst':
            base = self.mapping.get('cgst_output_prefix', 'Output CGST')
        elif tax_type == 'sgst':
            base = self.mapping.get('sgst_output_prefix', 'Output SGST')
        else:
            base = self.mapping.get('igst_output_prefix', 'Output IGST')

        ledger_name = f"{base} {half_rate:g}%" if split else base
        custom_override = self.mapping.get(ledger_name) or self.mapping.get(f"{tax_type}_{half_rate:g}")
        final_name = custom_override or ledger_name
        self.referenced_masters['duties_and_taxes'].add(final_name)
        return final_name

    def _resolve_input_tax_ledger(self, tax_type: str, rate: Decimal) -> str:
        """Resolve CGST/SGST/IGST input ledger name with CA mapping."""
        half_rate = (rate / Decimal('2')).normalize() if tax_type in ('cgst', 'sgst') else rate.normalize()
        split = self.mapping.get('split_tax_by_rate', True)
        if tax_type == 'cgst':
            base = self.mapping.get('cgst_input_prefix', 'Input CGST')
        elif tax_type == 'sgst':
            base = self.mapping.get('sgst_input_prefix', 'Input SGST')
        else:
            base = self.mapping.get('igst_input_prefix', 'Input IGST')

        ledger_name = f"{base} {half_rate:g}%" if split else base
        custom_override = self.mapping.get(ledger_name) or self.mapping.get(f"{tax_type}_{half_rate:g}")
        final_name = custom_override or ledger_name
        self.referenced_masters['duties_and_taxes'].add(final_name)
        return final_name

    def _resolve_sales_ledger(self, is_interstate: bool) -> str:
        if is_interstate and self.mapping.get('separate_interstate_sales', False):
            name = self.mapping.get('interstate_sales_account', 'Interstate Sales Account')
        else:
            name = self.mapping.get('sales_account', 'Sales Account')
        self.referenced_masters['sales_ledgers'].add(name)
        return name

    def _resolve_purchase_ledger(self, is_interstate: bool) -> str:
        if is_interstate and self.mapping.get('separate_interstate_purchases', False):
            name = self.mapping.get('interstate_purchase_account', 'Interstate Purchase Account')
        else:
            name = self.mapping.get('purchase_account', 'Purchase Account')
        self.referenced_masters['purchase_ledgers'].add(name)
        return name

    def _resolve_round_off_ledger(self) -> str:
        name = self.mapping.get('round_off_account', 'Round Off')
        return name

    # -------------------------------------------------------------------------
    # VOUCHER BUILDERS
    # -------------------------------------------------------------------------

    def _build_sales_invoice_voucher(self, inv) -> str:
        """Build a Sales Voucher XML snippet from a SalesInvoice instance."""
        inv_date = _format_tally_date(inv.invoice_date)
        inv_no = _xml_escape(inv.invoice_number)
        party_name = _xml_escape(inv.customer_name or getattr(inv.customer, 'name', 'Cash Sales'))
        
        # Track master
        cust = inv.customer
        self.referenced_masters['sundry_debtors'][party_name] = {
            'gstin': _xml_escape(getattr(cust, 'gstin', '') or ''),
            'state': _xml_escape(getattr(cust, 'state', '') or ''),
            'address': _xml_escape(inv.customer_address or getattr(cust, 'address', '') or ''),
        }

        # Determine interstate
        pos = inv.place_of_supply or getattr(cust, 'state', None)
        pos_code = get_pos_state_code(pos, fallback_state=self.seller_state_code)
        is_interstate = (self.seller_state_code != pos_code)

        sales_ledger = self._resolve_sales_ledger(is_interstate)
        round_off_ledger = self._resolve_round_off_ledger()

        # Compute line totals & tax breakdown
        total_taxable = Decimal('0.00')
        tax_aggregates = {}  # (tax_type, rate) -> Decimal(amount)
        inventory_xml_snippets = []

        items = inv.items.all().select_related('product', 'batch')
        for item in items:
            if getattr(item, 'row_type', 'item') == 'note':
                continue

            taxable, tax_amt, cgst, sgst, igst, rate = calculate_item_tax_breakup(item, is_interstate=is_interstate)
            total_taxable += taxable

            if is_interstate:
                if igst > 0:
                    tax_aggregates[('igst', rate)] = tax_aggregates.get(('igst', rate), Decimal('0.00')) + igst
            else:
                if cgst > 0:
                    tax_aggregates[('cgst', rate)] = tax_aggregates.get(('cgst', rate), Decimal('0.00')) + cgst
                if sgst > 0:
                    tax_aggregates[('sgst', rate)] = tax_aggregates.get(('sgst', rate), Decimal('0.00')) + sgst

            if self.mode == 'full_inventory' and item.product:
                p_name = _xml_escape(item.product.name)
                unit_str = (item.unit or item.product.unit or 'pcs').strip()
                self.referenced_masters['stock_items'][p_name] = {
                    'unit': unit_str,
                    'hsn': _xml_escape(item.hsn_sac_code or item.product.hsn_sac_code or ''),
                    'tax_rate': rate,
                }
                
                billed_qty = Decimal(str(item.quantity or 0))
                free_qty = Decimal(str(getattr(item, 'free_quantity', 0) or 0))
                actual_qty = billed_qty + free_qty

                price = Decimal(str(item.price or 0))
                rate_str = f"{price:.2f}/{unit_str}" if price else f"0.00/{unit_str}"
                
                godown_name = _xml_escape(inv.warehouse.name if inv.warehouse else 'Main Godown')
                self.referenced_masters['godowns'].add(godown_name)

                batch_xml = ""
                if item.batch:
                    b_name = _xml_escape(item.batch.batch_number)
                    exp_date = _format_tally_date(item.batch.expiry_date)
                    exp_tag = f"<EXPIRYPERIOD>{exp_date}</EXPIRYPERIOD>" if exp_date else ""
                    mfg_date = _format_tally_date(item.batch.manufacturing_date)
                    mfg_tag = f"<MFDON>{mfg_date}</MFDON>" if mfg_date else ""

                    batch_xml = f"""
                  <BATCHALLOCATIONS.LIST>
                    <GODOWNNAME>{godown_name}</GODOWNNAME>
                    <BATCHNAME>{b_name}</BATCHNAME>
                    {mfg_tag}
                    {exp_tag}
                    <AMOUNT>{taxable:.2f}</AMOUNT>
                    <ACTUALQTY>{_fmt_qty(actual_qty, unit_str)}</ACTUALQTY>
                    <BILLEDQTY>{_fmt_qty(billed_qty, unit_str)}</BILLEDQTY>
                  </BATCHALLOCATIONS.LIST>"""

                inv_line = f"""
            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{p_name}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <RATE>{rate_str}</RATE>
              <AMOUNT>{taxable:.2f}</AMOUNT>
              <ACTUALQTY>{_fmt_qty(actual_qty, unit_str)}</ACTUALQTY>
              <BILLEDQTY>{_fmt_qty(billed_qty, unit_str)}</BILLEDQTY>
              {batch_xml}
            </ALLINVENTORYENTRIES.LIST>"""
                inventory_xml_snippets.append(inv_line)

        # Voucher Amount Balancing
        # In Tally:
        # Party Debit: ISDEEMEDPOSITIVE = Yes, Amount = -Total
        # Sales Credit: ISDEEMEDPOSITIVE = No, Amount = Taxable
        # Tax Credits: ISDEEMEDPOSITIVE = No, Amount = Tax
        # Round Off: Balances Total - (Taxable + Taxes)
        total_inv_amount = Decimal(str(inv.total_amount or 0))
        computed_subtotal = total_taxable + sum(tax_aggregates.values(), Decimal('0.00'))
        round_off_diff = (total_inv_amount - computed_subtotal).quantize(Decimal('0.01'))

        party_ledger_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{party_name}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_inv_amount:.2f}</AMOUNT>
              <BILLALLOCATIONS.LIST>
                <NAME>{inv_no}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>-{total_inv_amount:.2f}</AMOUNT>
              </BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>"""

        sales_ledger_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sales_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_taxable:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        tax_ledger_entries = []
        for (tax_type, rate), amt in sorted(tax_aggregates.items()):
            if amt <= 0:
                continue
            tax_ledger = self._resolve_output_tax_ledger(tax_type, rate)
            entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{tax_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amt:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            tax_ledger_entries.append(entry)

        round_off_entry = ""
        if round_off_diff != Decimal('0.00'):
            # If round_off_diff > 0: customer pays more than subtotal (Round Off is Credit, ISDEEMEDPOSITIVE=No)
            # If round_off_diff < 0: discount/reduction (Round Off is Debit, ISDEEMEDPOSITIVE=Yes, Amount is negative)
            is_deemed = "No" if round_off_diff > 0 else "Yes"
            amt_str = f"{round_off_diff:.2f}" if round_off_diff > 0 else f"{round_off_diff:.2f}"
            round_off_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{round_off_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>{is_deemed}</ISDEEMEDPOSITIVE>
              <AMOUNT>{amt_str}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        inv_xml_block = "\n".join(inventory_xml_snippets) if inventory_xml_snippets else ""
        taxes_xml_block = "\n".join(tax_ledger_entries)

        voucher_xml = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{inv_date}</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{inv_no}</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{party_name}</PARTYLEDGERNAME>
            <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
            {party_ledger_entry}
            {inv_xml_block}
            {sales_ledger_entry}
            {taxes_xml_block}
            {round_off_entry}
          </VOUCHER>
        </TALLYMESSAGE>"""
        return voucher_xml

    def _build_purchase_bill_voucher(self, bill) -> str:
        """Build a Purchase Voucher XML snippet from a PurchaseBill instance."""
        bill_date = _format_tally_date(bill.bill_date)
        bill_no = _xml_escape(bill.bill_number)
        vendor_name = _xml_escape(bill.vendor_name or (bill.vendor.name if bill.vendor else 'Cash Purchase'))

        # Track master
        v = bill.vendor
        self.referenced_masters['sundry_creditors'][vendor_name] = {
            'gstin': _xml_escape(getattr(v, 'gstin', '') or bill.vendor_gstin or ''),
            'state': _xml_escape(getattr(v, 'state', '') or ''),
            'address': _xml_escape(bill.vendor_address or getattr(v, 'address', '') or ''),
        }

        # Inter-state determination
        pos = getattr(v, 'state', None) or bill.place_of_supply
        pos_code = get_pos_state_code(pos, fallback_state=self.seller_state_code)
        is_interstate = (self.seller_state_code != pos_code)

        purchase_ledger = self._resolve_purchase_ledger(is_interstate)
        round_off_ledger = self._resolve_round_off_ledger()

        total_taxable = Decimal('0.00')
        tax_aggregates = {}
        inventory_xml_snippets = []

        items = bill.items.all().select_related('product', 'batch')
        for item in items:
            taxable, tax_amt, cgst, sgst, igst, rate = calculate_item_tax_breakup(item, is_interstate=is_interstate)
            total_taxable += taxable

            if is_interstate:
                if igst > 0:
                    tax_aggregates[('igst', rate)] = tax_aggregates.get(('igst', rate), Decimal('0.00')) + igst
            else:
                if cgst > 0:
                    tax_aggregates[('cgst', rate)] = tax_aggregates.get(('cgst', rate), Decimal('0.00')) + cgst
                if sgst > 0:
                    tax_aggregates[('sgst', rate)] = tax_aggregates.get(('sgst', rate), Decimal('0.00')) + sgst

            if self.mode == 'full_inventory' and item.product:
                p_name = _xml_escape(item.product.name)
                unit_str = (item.unit or item.product.unit or 'pcs').strip()
                self.referenced_masters['stock_items'][p_name] = {
                    'unit': unit_str,
                    'hsn': _xml_escape(item.hsn_sac_code or item.product.hsn_sac_code or ''),
                    'tax_rate': rate,
                }
                billed_qty = Decimal(str(item.quantity or 0))
                free_qty = Decimal(str(getattr(item, 'free_quantity', 0) or 0))
                actual_qty = billed_qty + free_qty
                price = Decimal(str(item.price or 0))
                rate_str = f"{price:.2f}/{unit_str}" if price else f"0.00/{unit_str}"
                
                godown_name = _xml_escape(bill.warehouse.name if bill.warehouse else 'Main Godown')
                self.referenced_masters['godowns'].add(godown_name)

                batch_xml = ""
                if item.batch:
                    b_name = _xml_escape(item.batch.batch_number)
                    exp_date = _format_tally_date(item.batch.expiry_date)
                    exp_tag = f"<EXPIRYPERIOD>{exp_date}</EXPIRYPERIOD>" if exp_date else ""
                    mfg_date = _format_tally_date(item.batch.manufacturing_date)
                    mfg_tag = f"<MFDON>{mfg_date}</MFDON>" if mfg_date else ""

                    batch_xml = f"""
                  <BATCHALLOCATIONS.LIST>
                    <GODOWNNAME>{godown_name}</GODOWNNAME>
                    <BATCHNAME>{b_name}</BATCHNAME>
                    {mfg_tag}
                    {exp_tag}
                    <AMOUNT>-{taxable:.2f}</AMOUNT>
                    <ACTUALQTY>{_fmt_qty(actual_qty, unit_str)}</ACTUALQTY>
                    <BILLEDQTY>{_fmt_qty(billed_qty, unit_str)}</BILLEDQTY>
                  </BATCHALLOCATIONS.LIST>"""

                inv_line = f"""
            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{p_name}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <RATE>{rate_str}</RATE>
              <AMOUNT>-{taxable:.2f}</AMOUNT>
              <ACTUALQTY>{_fmt_qty(actual_qty, unit_str)}</ACTUALQTY>
              <BILLEDQTY>{_fmt_qty(billed_qty, unit_str)}</BILLEDQTY>
              {batch_xml}
            </ALLINVENTORYENTRIES.LIST>"""
                inventory_xml_snippets.append(inv_line)

        # Voucher Amount Balancing
        # Vendor is Credited: ISDEEMEDPOSITIVE = No, Amount = Total Bill Amount
        # Purchase is Debited: ISDEEMEDPOSITIVE = Yes, Amount = -Taxable
        # Input Taxes are Debited: ISDEEMEDPOSITIVE = Yes, Amount = -Tax
        total_bill_amount = Decimal(str(bill.total_amount or 0))
        computed_subtotal = total_taxable + sum(tax_aggregates.values(), Decimal('0.00'))
        round_off_diff = (total_bill_amount - computed_subtotal).quantize(Decimal('0.01'))

        vendor_ledger_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{vendor_name}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_bill_amount:.2f}</AMOUNT>
              <BILLALLOCATIONS.LIST>
                <NAME>{bill_no}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>{total_bill_amount:.2f}</AMOUNT>
              </BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>"""

        purchase_ledger_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{purchase_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_taxable:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        tax_ledger_entries = []
        for (tax_type, rate), amt in sorted(tax_aggregates.items()):
            if amt <= 0:
                continue
            tax_ledger = self._resolve_input_tax_ledger(tax_type, rate)
            entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{tax_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amt:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            tax_ledger_entries.append(entry)

        round_off_entry = ""
        if round_off_diff != Decimal('0.00'):
            is_deemed = "No" if round_off_diff > 0 else "Yes"
            amt_str = f"{round_off_diff:.2f}" if round_off_diff > 0 else f"{round_off_diff:.2f}"
            round_off_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{round_off_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>{is_deemed}</ISDEEMEDPOSITIVE>
              <AMOUNT>{amt_str}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        inv_xml_block = "\n".join(inventory_xml_snippets) if inventory_xml_snippets else ""
        taxes_xml_block = "\n".join(tax_ledger_entries)

        voucher_xml = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Invoice Voucher View">
            <DATE>{bill_date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{bill_no}</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{vendor_name}</PARTYLEDGERNAME>
            <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
            {vendor_ledger_entry}
            {inv_xml_block}
            {purchase_ledger_entry}
            {taxes_xml_block}
            {round_off_entry}
          </VOUCHER>
        </TALLYMESSAGE>"""
        return voucher_xml

    def _build_credit_note_voucher(self, cn) -> str:
        """Build a Credit Note (Sales Return) Voucher snippet."""
        cn_date = _format_tally_date(cn.date)
        cn_no = _xml_escape(cn.credit_note_number)
        customer_name = _xml_escape(cn.customer.name if cn.customer else 'Customer')

        self.referenced_masters['sundry_debtors'][customer_name] = {
            'gstin': _xml_escape(getattr(cn.customer, 'gstin', '') or ''),
            'state': _xml_escape(getattr(cn.customer, 'state', '') or ''),
            'address': _xml_escape(getattr(cn.customer, 'address', '') or ''),
        }

        pos = getattr(cn.customer, 'state', None)
        pos_code = get_pos_state_code(pos, fallback_state=self.seller_state_code)
        is_interstate = (self.seller_state_code != pos_code)

        sales_return_ledger = self.mapping.get('sales_return_account', 'Sales Returns')
        self.referenced_masters['sales_ledgers'].add(sales_return_ledger)
        round_off_ledger = self._resolve_round_off_ledger()

        total_taxable = Decimal('0.00')
        tax_aggregates = {}

        for item in cn.items.all().select_related('product', 'batch'):
            taxable, tax_amt, cgst, sgst, igst, rate = calculate_item_tax_breakup(item, is_interstate=is_interstate)
            total_taxable += taxable
            if is_interstate:
                if igst > 0:
                    tax_aggregates[('igst', rate)] = tax_aggregates.get(('igst', rate), Decimal('0.00')) + igst
            else:
                if cgst > 0:
                    tax_aggregates[('cgst', rate)] = tax_aggregates.get(('cgst', rate), Decimal('0.00')) + cgst
                if sgst > 0:
                    tax_aggregates[('sgst', rate)] = tax_aggregates.get(('sgst', rate), Decimal('0.00')) + sgst

        total_cn_amount = Decimal(str(cn.total_amount or 0))
        computed_subtotal = total_taxable + sum(tax_aggregates.values(), Decimal('0.00'))
        round_off_diff = (total_cn_amount - computed_subtotal).quantize(Decimal('0.01'))

        # Original invoice reference tags
        orig_ref_tags = ""
        if cn.original_invoice:
            orig_no = _xml_escape(cn.original_invoice.invoice_number)
            orig_date = _format_tally_date(cn.original_invoice.invoice_date)
            orig_ref_tags = f"""
            <ORIGINVOICENO>{orig_no}</ORIGINVOICENO>
            <ORIGINVOICEDATE>{orig_date}</ORIGINVOICEDATE>"""

        # In Credit Note:
        # Customer is Credited (balance decreases): ISDEEMEDPOSITIVE = No, Amount = +Total
        # Sales Return is Debited: ISDEEMEDPOSITIVE = Yes, Amount = -Taxable
        # Tax is Debited (reversed): ISDEEMEDPOSITIVE = Yes, Amount = -Tax
        customer_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{customer_name}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_cn_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        sales_return_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sales_return_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_taxable:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        tax_entries = []
        for (tax_type, rate), amt in sorted(tax_aggregates.items()):
            if amt <= 0:
                continue
            tax_ledger = self._resolve_output_tax_ledger(tax_type, rate)
            entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{tax_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amt:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            tax_entries.append(entry)

        taxes_xml_block = "\n".join(tax_entries)

        voucher_xml = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Credit Note" ACTION="Create">
            <DATE>{cn_date}</DATE>
            <VOUCHERTYPENAME>Credit Note</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{cn_no}</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{customer_name}</PARTYLEDGERNAME>
            {orig_ref_tags}
            {customer_entry}
            {sales_return_entry}
            {taxes_xml_block}
          </VOUCHER>
        </TALLYMESSAGE>"""
        return voucher_xml

    def _build_debit_note_voucher(self, dn) -> str:
        """Build a Debit Note (Purchase Return) Voucher snippet."""
        dn_date = _format_tally_date(dn.date)
        dn_no = _xml_escape(dn.debit_note_number)
        vendor_name = _xml_escape(dn.vendor_name or 'Vendor')

        self.referenced_masters['sundry_creditors'][vendor_name] = {
            'gstin': _xml_escape(dn.vendor_gstin or ''),
            'state': '',
            'address': '',
        }

        # Assume local if vendor state unknown
        is_interstate = False
        purchase_return_ledger = self.mapping.get('purchase_return_account', 'Purchase Returns')
        self.referenced_masters['purchase_ledgers'].add(purchase_return_ledger)

        total_taxable = Decimal('0.00')
        tax_aggregates = {}

        for item in dn.items.all().select_related('product', 'batch'):
            taxable, tax_amt, cgst, sgst, igst, rate = calculate_item_tax_breakup(item, is_interstate=is_interstate)
            total_taxable += taxable
            if is_interstate:
                if igst > 0:
                    tax_aggregates[('igst', rate)] = tax_aggregates.get(('igst', rate), Decimal('0.00')) + igst
            else:
                if cgst > 0:
                    tax_aggregates[('cgst', rate)] = tax_aggregates.get(('cgst', rate), Decimal('0.00')) + cgst
                if sgst > 0:
                    tax_aggregates[('sgst', rate)] = tax_aggregates.get(('sgst', rate), Decimal('0.00')) + sgst

        total_dn_amount = Decimal(str(dn.total_amount or 0))

        orig_ref_tags = ""
        if dn.original_bill:
            orig_no = _xml_escape(dn.original_bill.bill_number)
            orig_date = _format_tally_date(dn.original_bill.bill_date)
            orig_ref_tags = f"""
            <ORIGINVOICENO>{orig_no}</ORIGINVOICENO>
            <ORIGINVOICEDATE>{orig_date}</ORIGINVOICEDATE>"""

        # In Debit Note:
        # Vendor is Debited: ISDEEMEDPOSITIVE = Yes, Amount = -Total
        # Purchase Return is Credited: ISDEEMEDPOSITIVE = No, Amount = Taxable
        # Input Tax is Credited: ISDEEMEDPOSITIVE = No, Amount = Tax
        vendor_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{vendor_name}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{total_dn_amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        purchase_return_entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{purchase_return_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total_taxable:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""

        tax_entries = []
        for (tax_type, rate), amt in sorted(tax_aggregates.items()):
            if amt <= 0:
                continue
            tax_ledger = self._resolve_input_tax_ledger(tax_type, rate)
            entry = f"""
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{tax_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amt:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>"""
            tax_entries.append(entry)

        taxes_xml_block = "\n".join(tax_entries)

        voucher_xml = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Debit Note" ACTION="Create">
            <DATE>{dn_date}</DATE>
            <VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>
            <VOUCHERNUMBER>{dn_no}</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{vendor_name}</PARTYLEDGERNAME>
            {orig_ref_tags}
            {vendor_entry}
            {purchase_return_entry}
            {taxes_xml_block}
          </VOUCHER>
        </TALLYMESSAGE>"""
        return voucher_xml

    # -------------------------------------------------------------------------
    # MASTER LEDGER BUILDER
    # -------------------------------------------------------------------------

    def _build_masters_xml(self) -> str:
        """
        Generate <TALLYMESSAGE> entries for all referenced ledgers, groups, and stock items.
        Ensures that missing accounts are automatically created in Tally without import errors.
        """
        master_snippets = []

        # 1. Sundry Debtors
        for name, meta in sorted(self.referenced_masters['sundry_debtors'].items()):
            gstin_tag = f"<PARTYGSTIN>{meta['gstin']}</PARTYGSTIN>" if meta['gstin'] else ""
            state_tag = f"<LEDSTATENAME>{meta['state']}</LEDSTATENAME>" if meta['state'] else ""
            addr_tag = f"<ADDRESS.LIST><ADDRESS>{meta['address']}</ADDRESS></ADDRESS.LIST>" if meta['address'] else ""
            snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <ISBILLWISEON>Yes</ISBILLWISEON>
            <COUNTRYNAME>India</COUNTRYNAME>
            {state_tag}
            {gstin_tag}
            {addr_tag}
          </LEDGER>
        </TALLYMESSAGE>"""
            master_snippets.append(snip)

        # 2. Sundry Creditors
        for name, meta in sorted(self.referenced_masters['sundry_creditors'].items()):
            gstin_tag = f"<PARTYGSTIN>{meta['gstin']}</PARTYGSTIN>" if meta['gstin'] else ""
            state_tag = f"<LEDSTATENAME>{meta['state']}</LEDSTATENAME>" if meta['state'] else ""
            addr_tag = f"<ADDRESS.LIST><ADDRESS>{meta['address']}</ADDRESS></ADDRESS.LIST>" if meta['address'] else ""
            snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <PARENT>Sundry Creditors</PARENT>
            <ISBILLWISEON>Yes</ISBILLWISEON>
            <COUNTRYNAME>India</COUNTRYNAME>
            {state_tag}
            {gstin_tag}
            {addr_tag}
          </LEDGER>
        </TALLYMESSAGE>"""
            master_snippets.append(snip)

        # 3. Sales Ledgers
        for name in sorted(self.referenced_masters['sales_ledgers']):
            snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <PARENT>Sales Accounts</PARENT>
            <ISINVENTORYAFFECTED>No</ISINVENTORYAFFECTED>
          </LEDGER>
        </TALLYMESSAGE>"""
            master_snippets.append(snip)

        # 4. Purchase Ledgers
        for name in sorted(self.referenced_masters['purchase_ledgers']):
            snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <PARENT>Purchase Accounts</PARENT>
            <ISINVENTORYAFFECTED>No</ISINVENTORYAFFECTED>
          </LEDGER>
        </TALLYMESSAGE>"""
            master_snippets.append(snip)

        # 5. Duties & Taxes
        for name in sorted(self.referenced_masters['duties_and_taxes']):
            snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <PARENT>Duties &amp; Taxes</PARENT>
            <TAXTYPE>GST</TAXTYPE>
          </LEDGER>
        </TALLYMESSAGE>"""
            master_snippets.append(snip)

        # 6. Round Off Ledger
        round_off_name = self._resolve_round_off_ledger()
        snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{round_off_name}" ACTION="Create">
            <NAME>{round_off_name}</NAME>
            <PARENT>Indirect Expenses</PARENT>
          </LEDGER>
        </TALLYMESSAGE>"""
        master_snippets.append(snip)

        # 7. Stock Items (if full inventory mode)
        if self.mode == 'full_inventory':
            for name, meta in sorted(self.referenced_masters['stock_items'].items()):
                u_str = meta['unit'] or 'pcs'
                hsn_tag = f"<HSNCODE>{meta['hsn']}</HSNCODE>" if meta['hsn'] else ""
                snip = f"""
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <STOCKITEM NAME="{name}" ACTION="Create">
            <NAME>{name}</NAME>
            <BASEUNITS>{u_str}</BASEUNITS>
            {hsn_tag}
          </STOCKITEM>
        </TALLYMESSAGE>"""
                master_snippets.append(snip)

        return "\n".join(master_snippets)

    # -------------------------------------------------------------------------
    # MAIN EXPORT ENTRYPOINT
    # -------------------------------------------------------------------------

    def generate_xml(self) -> str:
        """
        Query database, build vouchers, build masters, and return full Tally XML envelope.
        """
        from billing.models import SalesInvoice, PurchaseBill
        from billing.models_returns import CreditNote, DebitNote

        voucher_snippets = []

        # 1. Fetch Sales Invoices
        if self.include_sales:
            sales_qs = SalesInvoice.objects.filter(
                created_by=self.tenant,
                status='final',
            ).select_related('customer', 'warehouse').prefetch_related('items__product', 'items__batch')

            if self.start_date:
                sales_qs = sales_qs.filter(invoice_date__gte=self.start_date)
            if self.end_date:
                sales_qs = sales_qs.filter(invoice_date__lte=self.end_date)

            for inv in sales_qs.order_by('invoice_date', 'invoice_number'):
                try:
                    v_xml = self._build_sales_invoice_voucher(inv)
                    voucher_snippets.append(v_xml)
                except Exception as e:
                    logger.exception("Failed to export sales invoice %s: %s", getattr(inv, 'invoice_number', 'unknown'), e)

        # 2. Fetch Purchase Bills
        if self.include_purchases:
            pur_qs = PurchaseBill.objects.filter(
                created_by=self.tenant
            ).select_related('vendor', 'warehouse').prefetch_related('items__product', 'items__batch')

            if self.start_date:
                pur_qs = pur_qs.filter(bill_date__gte=self.start_date)
            if self.end_date:
                pur_qs = pur_qs.filter(bill_date__lte=self.end_date)

            for bill in pur_qs.order_by('bill_date', 'bill_number'):
                try:
                    v_xml = self._build_purchase_bill_voucher(bill)
                    voucher_snippets.append(v_xml)
                except Exception as e:
                    logger.exception("Failed to export purchase bill %s: %s", getattr(bill, 'bill_number', 'unknown'), e)

        # 3. Fetch Returns (Credit & Debit Notes)
        if self.include_returns:
            cn_qs = CreditNote.objects.filter(
                created_by=self.tenant
            ).select_related('customer', 'original_invoice').prefetch_related('items__product', 'items__batch')

            if self.start_date:
                cn_qs = cn_qs.filter(date__gte=self.start_date)
            if self.end_date:
                cn_qs = cn_qs.filter(date__lte=self.end_date)

            for cn in cn_qs.order_by('date', 'credit_note_number'):
                try:
                    v_xml = self._build_credit_note_voucher(cn)
                    voucher_snippets.append(v_xml)
                except Exception as e:
                    logger.exception("Failed to export credit note %s: %s", getattr(cn, 'credit_note_number', 'unknown'), e)

            dn_qs = DebitNote.objects.filter(
                created_by=self.tenant
            ).select_related('original_bill').prefetch_related('items__product', 'items__batch')

            if self.start_date:
                dn_qs = dn_qs.filter(date__gte=self.start_date)
            if self.end_date:
                dn_qs = dn_qs.filter(date__lte=self.end_date)

            for dn in dn_qs.order_by('date', 'debit_note_number'):
                try:
                    v_xml = self._build_debit_note_voucher(dn)
                    voucher_snippets.append(v_xml)
                except Exception as e:
                    logger.exception("Failed to export debit note %s: %s", getattr(dn, 'debit_note_number', 'unknown'), e)

        # Generate Masters after vouchers so all referenced ledgers have been cataloged
        masters_xml = self._build_masters_xml()
        vouchers_xml = "\n".join(voucher_snippets)

        full_envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
{masters_xml}
      </REQUESTDATA>
    </IMPORTDATA>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
{vouchers_xml}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
        return full_envelope.strip()

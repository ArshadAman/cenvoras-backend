"""
UAT P1 item 13 (Rudra CA): Rule 46 CGST Rules essentials on a final tax invoice.

* Serial number: unique per financial year (Cenvora already enforces unique per business,
  which is stricter), at most 16 characters, only A-Z, 0-9, '-' and '/'.
* Invoice date.
* Recipient: name always; address too when the recipient is registered (has a GSTIN) or,
  for an unregistered recipient, when the taxable value is ₹50,000 or more.
* Each line: HSN (already checked by validate_hsn_sac_code: 4 digits, 6 above ₹5 Cr),
  quantity > 0 and a unit (UQC), taxable value, rate and tax per head (computed by the ledger
  from the line rate).
* Place of supply (falls back to the customer's state, then the supplier's state).
* Whether tax is payable on reverse charge: SalesInvoice.is_reverse_charge.
"""
import re
from decimal import Decimal

SERIAL_RE = re.compile(r'^[A-Z0-9/-]{1,16}$')
UNREGISTERED_ADDRESS_THRESHOLD = Decimal('50000')


def serial_error(number):
    n = str(number or '').strip()
    if not n:
        return 'Invoice number is required.'
    if len(n) > 16:
        return f'Invoice number "{n}" is {len(n)} characters; Rule 46 allows at most 16.'
    if not SERIAL_RE.fullmatch(n):
        return (f'Invoice number "{n}" can only use capital letters A-Z, digits 0-9, "-" and "/" (Rule 46). '
                'Change the invoice prefix in Settings if needed.')
    return None


def rule46_errors(*, invoice_number, invoice_date, customer_name, customer_address, customer_gstin,
                  items, taxable_total):
    """Return {field: message} for a final invoice, empty when it complies."""
    from billing.sequence_service import is_placeholder_number
    errors = {}
    if invoice_number and not is_placeholder_number(invoice_number):
        err = serial_error(invoice_number)
        if err:
            errors['invoice_number'] = err
    if not invoice_date:
        errors['invoice_date'] = 'Invoice date is required (Rule 46).'
    if not str(customer_name or '').strip():
        errors['customer_name'] = "Recipient's name is required (Rule 46)."
    needs_address = bool(str(customer_gstin or '').strip()) or (
        Decimal(str(taxable_total or 0)) >= UNREGISTERED_ADDRESS_THRESHOLD)
    if needs_address and not str(customer_address or '').strip():
        errors['customer_address'] = (
            "Recipient's address is required (Rule 46: registered recipient, or unregistered recipient "
            "with taxable value of ₹50,000 or more).")
    line_errors = []
    for idx, item in enumerate(items or []):
        if (item.get('row_type') or 'item') == 'note':
            continue
        q = Decimal(str(item.get('quantity') or 0))
        if q <= 0:
            line_errors.append(f'Line {idx + 1}: quantity must be more than 0.')
        if not str(item.get('unit') or '').strip():
            product = item.get('product')
            if not str(getattr(product, 'unit', '') or '').strip():
                line_errors.append(f'Line {idx + 1}: unit (UQC) is required.')
    if line_errors:
        errors['items'] = line_errors
    return errors

from django.db.models import Q
from django.utils import timezone
from billing.models import SalesInvoice, Customer
from inventory.models import Product, Warehouse
from billing.serializers import SalesInvoiceSerializer
from decimal import Decimal
import logging
import re

logger = logging.getLogger(__name__)

def search_customers(user, query):
    tenant = getattr(user, 'active_tenant', user)
    return Customer.objects.filter(
        Q(name__icontains=query) | Q(phone__icontains=query),
        created_by=tenant
    )[:5]

def search_products(user, query):
    tenant = getattr(user, 'active_tenant', user)
    return Product.objects.filter(
        name__icontains=query,
        created_by=tenant
    )[:5]


def get_next_invoice_number_internal(user, prefix='INV-'):
    from billing.sequence_service import allocate_next_number
    tenant = getattr(user, 'active_tenant', user)
    return allocate_next_number(tenant=tenant, document_type='sales_invoice', prefix=prefix)


def _default_warehouse_id(tenant):
    wh = Warehouse.objects.filter(created_by=tenant, is_active=True).order_by('name').first()
    if not wh:
        wh = Warehouse.objects.filter(created_by=tenant).order_by('name').first()
    return str(wh.id) if wh else None


def enrich_invoice_draft_entities(user, entities):
    """
    Build a SalesForm-ready draft payload without allocating invoice numbers
    or writing invoices. Used for AI → Review & Create Invoice deep-link.
    """
    tenant = getattr(user, 'active_tenant', user)
    data = dict(entities or {})
    data.pop('invoice_number', None)

    customer_id = data.get('customer_id')
    if customer_id:
        try:
            customer = Customer.objects.get(id=customer_id, created_by=tenant)
            data['customer_id'] = str(customer.id)
            data['customer_name'] = customer.name
            data['customer_email'] = customer.email or data.get('customer_email') or ''
            data['customer_phone'] = customer.phone or data.get('customer_phone') or ''
            data['customer_address'] = customer.address or data.get('customer_address') or ''
            data['customer_gstin'] = customer.gstin or data.get('customer_gstin') or ''
            if getattr(customer, 'state', None):
                data['place_of_supply'] = customer.state
        except (Customer.DoesNotExist, ValueError, TypeError):
            pass

    enriched_items = []
    for raw in data.get('items') or []:
        item = dict(raw or {})
        name = (item.get('product_name') or item.get('product') or item.get('name') or '').strip()
        qty = Decimal(str(item.get('quantity') or 1))
        product = None
        product_id = item.get('product_id')
        if product_id:
            product = Product.objects.filter(id=product_id, created_by=tenant).first()
        if not product and name:
            product = Product.objects.filter(name__iexact=name, created_by=tenant).first()
            if not product:
                product = Product.objects.filter(name__icontains=name, created_by=tenant).first()
        if product:
            price = item.get('price')
            if price is None:
                price = product.sale_price if product.sale_price is not None else (product.price or 0)
            tax = item.get('tax')
            if tax is None:
                tax = product.tax or 0
            enriched_items.append({
                'product_id': str(product.id),
                'product_name': product.name,
                'description': item.get('description') or product.description or '',
                'product_description': item.get('description') or product.description or '',
                'quantity': float(qty),
                'price': float(price or 0),
                'tax': float(tax or 0),
                'unit': item.get('unit') or product.unit or 'pcs',
                'hsn_sac_code': item.get('hsn_sac_code') or product.hsn_sac_code or '',
                'manufacturer': item.get('manufacturer') or getattr(product, 'manufacturer', '') or '',
                'isExistingProduct': True,
            })
        else:
            price = float(item.get('price') or 0)
            enriched_items.append({
                'product_id': None,
                'product_name': name,
                'description': item.get('description') or '',
                'product_description': item.get('description') or '',
                'quantity': float(qty),
                'price': price,
                'tax': float(item.get('tax') or 0),
                'unit': item.get('unit') or 'pcs',
                'hsn_sac_code': item.get('hsn_sac_code') or '',
                'manufacturer': item.get('manufacturer') or '',
                'isExistingProduct': False,
            })

    data['items'] = enriched_items
    if not data.get('warehouse'):
        wh_id = _default_warehouse_id(tenant)
        if wh_id:
            data['warehouse'] = wh_id
    if not data.get('invoice_date'):
        data['invoice_date'] = timezone.localdate().isoformat()
    data['status'] = 'draft'
    return data


def create_invoice_from_ai(user, entities, request=None):
    """
    Creates a final invoice from AI extracted entities.
    Does NOT pre-allocate sequence numbers — serializer allocates on successful save.
    Prefer enrich_invoice_draft_entities + SalesForm for user review when possible.
    """
    tenant = getattr(user, 'active_tenant', user)

    data = enrich_invoice_draft_entities(user, entities)
    # Final create path — clear draft flag; leave number empty for allocate-on-save
    data['status'] = 'final'
    data.pop('invoice_number', None)

    if not data.get('invoice_date'):
        data['invoice_date'] = timezone.now().date().isoformat()

    if 'items' in data:
        for item in data['items']:
            if item.get('price') is None:
                item['price'] = 0
            if item.get('quantity') is None:
                item['quantity'] = 1
            # Serializer expects product PK when known
            if item.get('product_id') and not item.get('product'):
                item['product'] = item['product_id']

    try:
        serializer = SalesInvoiceSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            invoice = serializer.save(created_by=tenant)
            return {
                "status": "success",
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "customer_name": invoice.customer_name,
                "total_amount": round(float(invoice.total_amount), 2)
            }

        else:
            logger.warning(f"AI Invoice Validation Failed: {serializer.errors}")
            return {
                "status": "error",
                "errors": serializer.errors,
                "entities": enrich_invoice_draft_entities(user, entities),
            }
    except Exception as e:
        logger.error(f"AI Invoice Creation Exception: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "entities": enrich_invoice_draft_entities(user, entities),
        }


_AMOUNT_RE = re.compile(
    # Accept "18L" / "18lakh" / "18 lakh" / "₹18,00,000"
    r'(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|lacs|lakhs|l(?![a-z])|cr|crore|crores)?',
    re.I,
)


def answer_tds_question(question, user=None):
    """
    If the question is about TDS / income tax on a salary figure, answer via
    preview_monthly_tds / compute_tax_on_income. Returns None if not a TDS Q.
    When an employee name is mentioned, prefer their CTC from HR.
    """
    q = (question or '').strip()
    q_lower = q.lower()
    tds_hints = ('tds', 'income tax', 'tax on', 'taxable', 'new regime', 'old regime', 'section 192', 'salary')
    if not any(h in q_lower for h in tds_hints):
        return None

    from hr.services.tds_service import preview_monthly_tds, compute_tax_on_income, compute_dynamic_tds

    regime = 'old' if 'old regime' in q_lower else 'new'
    annual = None
    monthly = None
    source_note = 'assumed from question figure'

    # Prefer live employee CTC when a name is referenced
    if user is not None:
        try:
            from hr.models import Employee, EmployeeSalaryAssignment
            tenant = getattr(user, 'active_tenant', user)
            emp = None
            for candidate in Employee.objects.filter(tenant=tenant, status='active'):
                name = (candidate.full_name or '').strip().lower()
                if name and name in q_lower:
                    emp = candidate
                    break
            if emp is None:
                # also match "18L employee" / employee_code
                for candidate in Employee.objects.filter(tenant=tenant, status='active'):
                    code = (candidate.employee_code or '').strip().lower()
                    if code and code in q_lower:
                        emp = candidate
                        break
            if emp is not None:
                assignment = EmployeeSalaryAssignment.objects.filter(
                    employee=emp
                ).order_by('-effective_from').first()
                if assignment and assignment.monthly_ctc:
                    monthly = Decimal(str(assignment.monthly_ctc)).quantize(Decimal('0.01'))
                    annual = (monthly * Decimal('12')).quantize(Decimal('0.01'))
                    regime = (emp.tax_regime or regime or 'new').lower()
                    source_note = f"from {emp.full_name}'s CTC"
                    today = __import__('datetime').date.today()
                    tds_data = compute_dynamic_tds(emp, monthly, today.month, today.year)
                    return (
                        f"**TDS estimate ({regime} regime, FY {tds_data['financial_year']})**\n\n"
                        f"• Employee: **{emp.full_name}**\n"
                        f"• Monthly CTC / gross: **₹{monthly:,.2f}**\n"
                        f"• Projected annual gross: **₹{tds_data['projected_annual_gross']:,.2f}**\n"
                        f"• Net taxable income: **₹{tds_data['net_taxable_income']:,.2f}**\n"
                        f"• Annual tax (incl. 4% cess): **₹{tds_data['total_annual_tax']:,.2f}**\n"
                        f"• Monthly TDS: **₹{tds_data['monthly_tds']:,.2f}**\n\n"
                        f"_Computed by the payroll TDS engine ({source_note})._"
                    )
        except Exception:
            logger.exception('TDS Q: employee CTC lookup failed')

    q_norm = q.replace(',', '')
    matches = list(_AMOUNT_RE.finditer(q_norm))
    # Prefer matches that include an explicit unit (18L / 18 lakh).
    # If the question also has a bare monthly figure near "salary", still prefer
    # the lakh/crore amount for annual CTC questions ("₹18L employee", etc.).
    unit_match = next((m for m in matches if m.group(2)), None)
    salary_near = None
    for m_cand in matches:
        start = max(0, m_cand.start() - 24)
        window = q_norm[start:m_cand.end() + 12].lower()
        if 'salary' in window or 'ctc' in window or 'gross' in window:
            if not m_cand.group(2):
                salary_near = m_cand
                break
    # Annual unit wins for "18L TDS"; otherwise salary-near bare amount; else first match
    m = unit_match or salary_near or (matches[0] if matches else None)
    if m is None:
        m = re.search(
            r'(\d+(?:\.\d+)?)\s*(lakh|lac|lacs|lakhs|l(?![a-z])|cr|crore|crores)',
            q_lower,
        )
        if not m:
            return None
        amount = Decimal(m.group(1))
        unit = (m.group(2) or '').lower()
    else:
        amount = Decimal(m.group(1).replace(',', ''))
        unit = (m.group(2) or '').lower()

    if unit.startswith('l'):
        annual = amount * Decimal('100000')
    elif unit.startswith('c'):
        annual = amount * Decimal('10000000')
    else:
        # Heuristic: figures >= 1e5 treated as annual; else monthly CTC
        annual = amount if amount >= Decimal('100000') else amount * Decimal('12')

    monthly = (annual / Decimal('12')).quantize(Decimal('0.01'))
    preview = preview_monthly_tds(monthly_gross=monthly, regime=regime)
    annual_tax = preview['total_annual_tax']
    monthly_tds = preview['monthly_tds']
    taxable = preview['net_taxable_income']
    rebate = preview['rebate_amount']

    # Also expose tax-on-taxable for the classic “₹12L taxable” wording
    if 'taxable' in q_lower:
        base, rebate_applied, rebate_amt, cess, total = compute_tax_on_income(
            annual, regime=regime, fy_string=preview['financial_year']
        )
        annual_tax = total
        rebate = rebate_amt
        taxable = annual
        monthly_tds = (annual_tax / Decimal('12')).quantize(Decimal('0.01')) if annual_tax > 0 else Decimal('0.00')

    return (
        f"**TDS estimate ({regime} regime, FY {preview['financial_year']})**\n\n"
        f"• Assumed annual gross / base: **₹{annual:,.2f}** ({source_note})\n"
        f"• Net taxable income: **₹{taxable:,.2f}**\n"
        f"• Sec 87A rebate: **₹{rebate:,.2f}**\n"
        f"• Annual tax (incl. 4% cess): **₹{annual_tax:,.2f}**\n"
        f"• Monthly TDS: **₹{monthly_tds:,.2f}**\n\n"
        f"_Computed by the payroll TDS engine — not an LLM estimate._"
    )

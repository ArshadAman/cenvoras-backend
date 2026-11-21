"""Link a Customer and Vendor that represent the same party."""


def _norm_gstin(value):
    return (value or '').strip().upper()


def _norm_name(value):
    return ' '.join((value or '').split()).strip().lower()


def _linked_customer_of(vendor):
    try:
        return vendor.linked_customer
    except Exception:
        return None


def counterpart_payload(customer=None, vendor=None):
    payload = {}
    if customer is not None:
        linked = getattr(customer, 'linked_vendor', None)
        if linked:
            payload['linked_vendor'] = {
                'id': str(linked.id),
                'name': linked.name,
            }
        else:
            payload['linked_vendor'] = None
    if vendor is not None:
        linked = _linked_customer_of(vendor)
        if linked:
            payload['linked_customer'] = {
                'id': str(linked.id),
                'name': linked.name,
                'current_balance': float(linked.current_balance or 0),
            }
        else:
            payload['linked_customer'] = None
    return payload


def link_customer_vendor(customer, vendor):
    if not customer or not vendor:
        return customer
    if customer.linked_vendor_id == vendor.id:
        return customer
    customer.linked_vendor = vendor
    customer.save(update_fields=['linked_vendor'])
    return customer


def find_matching_vendor(customer, tenant):
    from billing.models import Vendor
    gstin = _norm_gstin(customer.gstin)
    if gstin:
        match = Vendor.objects.filter(created_by=tenant, gstin__iexact=gstin).first()
        if match:
            return match
    name = _norm_name(customer.name)
    if name:
        for vendor in Vendor.objects.filter(created_by=tenant).only('id', 'name'):
            if _norm_name(vendor.name) == name:
                return vendor
    return None


def find_matching_customer(vendor, tenant):
    from billing.models import Customer
    gstin = _norm_gstin(vendor.gstin)
    if gstin:
        match = Customer.objects.filter(created_by=tenant, gstin__iexact=gstin).first()
        if match:
            return match
    name = _norm_name(vendor.name)
    if name:
        for customer in Customer.objects.filter(created_by=tenant).only('id', 'name'):
            if _norm_name(customer.name) == name:
                return customer
    return None


def autolink_customer(customer, tenant):
    if not customer or customer.linked_vendor_id:
        return customer
    vendor = find_matching_vendor(customer, tenant)
    if vendor and not _linked_customer_of(vendor):
        return link_customer_vendor(customer, vendor)
    return customer


def autolink_vendor(vendor, tenant):
    if not vendor:
        return vendor
    if _linked_customer_of(vendor):
        return vendor
    customer = find_matching_customer(vendor, tenant)
    if customer and not customer.linked_vendor_id:
        link_customer_vendor(customer, vendor)
    return vendor


def ensure_vendor_for_customer(customer, tenant):
    """Return the vendor counterpart, creating one from the customer if needed."""
    from billing.models import Vendor

    if customer.linked_vendor_id:
        return customer.linked_vendor

    vendor = find_matching_vendor(customer, tenant)
    if not vendor:
        vendor = Vendor.objects.create(
            name=customer.name or 'Vendor',
            email=customer.email,
            phone=customer.phone,
            gstin=customer.gstin,
            address=customer.address,
            state=customer.state,
            created_by=tenant,
        )
    link_customer_vendor(customer, vendor)
    return vendor

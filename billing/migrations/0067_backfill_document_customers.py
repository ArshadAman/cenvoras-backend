import uuid
from django.db import migrations


def backfill_customers(apps, schema_editor):
    Customer = apps.get_model('billing', 'Customer')
    PartyMeta = apps.get_model('billing', 'PartyMeta')
    SalesOrder = apps.get_model('billing', 'SalesOrder')
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')
    Quotation = apps.get_model('billing', 'Quotation')
    DeliveryChallan = apps.get_model('billing', 'DeliveryChallan')

    doc_models = [
        (SalesOrder, 'sales orders'),
        (SalesInvoice, 'sales invoices'),
        (Quotation, 'quotations'),
        (DeliveryChallan, 'delivery challans'),
    ]

    for model_class, label in doc_models:
        # Find all documents with customer_name but no linked customer FK
        qs = model_class.objects.filter(customer__isnull=True).exclude(customer_name__isnull=True).exclude(customer_name='')
        for doc in qs.iterator():
            tenant = doc.created_by
            if not tenant:
                continue

            name = (doc.customer_name or '').strip()
            gstin = (getattr(doc, 'customer_gstin', None) or '').strip().upper()
            address = getattr(doc, 'customer_address', None)
            email = getattr(doc, 'customer_email', None)
            phone = getattr(doc, 'customer_phone', None)

            if not name and not gstin:
                continue

            customer = None
            # 1. Match by GSTIN first if provided
            if gstin:
                customer = Customer.objects.filter(created_by=tenant, gstin__iexact=gstin).first()

            # 2. Match by email second if provided
            if not customer and email:
                customer = Customer.objects.filter(created_by=tenant, email__iexact=str(email).strip()).first()

            # 3. Match by name third
            if not customer and name:
                customer = Customer.objects.filter(created_by=tenant, name__iexact=name).first()

            if customer:
                # Update missing fields on customer
                update_fields = []
                if address and not customer.address:
                    customer.address = address
                    update_fields.append('address')
                if gstin and not customer.gstin:
                    customer.gstin = gstin
                    update_fields.append('gstin')
                if phone and not customer.phone:
                    customer.phone = phone
                    update_fields.append('phone')
                if email and not customer.email:
                    customer.email = email
                    update_fields.append('email')
                if update_fields:
                    customer.save(update_fields=update_fields)
            else:
                # Create global Customer record
                customer = Customer.objects.create(
                    id=uuid.uuid4(),
                    name=name or gstin,
                    email=email or None,
                    phone=phone or None,
                    address=address or None,
                    gstin=gstin or None,
                    created_by=tenant,
                )

            # Ensure PartyMeta exists
            PartyMeta.objects.get_or_create(customer=customer)

            # Link customer to document
            doc.customer = customer
            doc.save(update_fields=['customer'])


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0066_rcm_receipt_date_self_invoice_payment_voucher'),
    ]

    operations = [
        migrations.RunPython(backfill_customers, reverse_code=migrations.RunPython.noop),
    ]

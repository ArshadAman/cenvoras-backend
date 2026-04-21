from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce

from .models import Customer, Payment, SalesInvoice

_AMOUNT_FIELD = DecimalField(max_digits=12, decimal_places=2)


def recompute_invoice_amount_paid(invoice_id):
    if not invoice_id:
        return

    invoice = SalesInvoice.objects.filter(pk=invoice_id).first()
    if not invoice:
        return

    paid_total = (
        Payment.objects.filter(invoice_id=invoice_id)
        .aggregate(total=Coalesce(Sum('amount'), Value(0, output_field=_AMOUNT_FIELD)))
        .get('total')
    )

    capped_paid = min(paid_total, invoice.total_amount)
    if capped_paid < 0:
        capped_paid = 0

    SalesInvoice.objects.filter(pk=invoice_id).update(amount_paid=capped_paid)
    invoice.amount_paid = capped_paid
    invoice.refresh_payment_status(save=True)


def recompute_customer_balance(customer_id):
    if not customer_id:
        return

    customer = Customer.objects.only('id', 'created_by').filter(pk=customer_id).first()
    if not customer:
        return

    outstanding_expr = ExpressionWrapper(
        Coalesce(F('total_amount'), Value(0, output_field=_AMOUNT_FIELD))
        - Coalesce(F('amount_paid'), Value(0, output_field=_AMOUNT_FIELD)),
        output_field=_AMOUNT_FIELD,
    )

    outstanding_total = (
        SalesInvoice.objects.filter(created_by=customer.created_by, customer_id=customer_id, status='final')
        .aggregate(total=Coalesce(Sum(outstanding_expr), Value(0, output_field=_AMOUNT_FIELD)))
        .get('total')
    )

    Customer.objects.filter(pk=customer_id).update(current_balance=outstanding_total)


def recompute_customer_balances_for_customers(customers, tenant):
    customer_ids = [customer.id for customer in customers]
    if not customer_ids:
        return

    outstanding_expr = ExpressionWrapper(
        Coalesce(F('total_amount'), Value(0, output_field=_AMOUNT_FIELD))
        - Coalesce(F('amount_paid'), Value(0, output_field=_AMOUNT_FIELD)),
        output_field=_AMOUNT_FIELD,
    )
    outstanding_rows = (
        SalesInvoice.objects.filter(created_by=tenant, status='final', customer_id__in=customer_ids)
        .values('customer_id')
        .annotate(total=Coalesce(Sum(outstanding_expr), Value(0, output_field=_AMOUNT_FIELD)))
    )

    outstanding_map = {row['customer_id']: row['total'] for row in outstanding_rows}

    for customer in customers:
        computed_balance = outstanding_map.get(customer.id, 0) or 0
        if customer.current_balance != computed_balance:
            Customer.objects.filter(pk=customer.id).update(current_balance=computed_balance)
            customer.current_balance = computed_balance

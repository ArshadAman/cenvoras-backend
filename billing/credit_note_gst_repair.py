"""
Idempotent repair for credit notes missing Output GST in the ledger / GSTR-1 CDNR.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum


def _expected_gst_split(credit_note):
    from billing.gst_utils import (
        get_seller_state_code,
        get_pos_state_code,
        calculate_item_tax_breakup,
    )

    user = credit_note.created_by
    tenant = getattr(user, 'active_tenant', user)
    seller = get_seller_state_code(tenant)
    customer = getattr(credit_note, 'customer', None)
    pos = get_pos_state_code(
        getattr(credit_note, 'customer_gstin', None)
        or (getattr(customer, 'gstin', None) if customer else None)
        or (getattr(customer, 'state', None) if customer else None)
        or getattr(credit_note, 'place_of_supply', None),
        fallback_state=seller,
    )
    interstate = bool(seller and pos and str(seller).strip() != str(pos).strip())

    items = list(credit_note.items.all()) if hasattr(credit_note, 'items') else []
    if not items:
        return None

    total_taxable = Decimal('0.00')
    total_cgst = Decimal('0.00')
    total_sgst = Decimal('0.00')
    total_igst = Decimal('0.00')
    for item in items:
        taxable, _tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_interstate=interstate)
        total_taxable += taxable
        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst

    expected_gst = total_cgst + total_sgst + total_igst
    if expected_gst <= Decimal('0.00'):
        return None

    return {
        'taxable': total_taxable,
        'cgst': total_cgst,
        'sgst': total_sgst,
        'igst': total_igst,
        'is_interstate': interstate,
    }


def _existing_gst_debits(credit_note):
    from ledger.models import GeneralLedgerEntry

    qs = GeneralLedgerEntry.objects.filter(credit_note=credit_note, debit__gt=0)
    igst = qs.filter(account__code='2203').aggregate(s=Sum('debit'))['s'] or Decimal('0.00')
    cgst = qs.filter(account__code='2201').aggregate(s=Sum('debit'))['s'] or Decimal('0.00')
    sgst = qs.filter(account__code='2202').aggregate(s=Sum('debit'))['s'] or Decimal('0.00')
    return igst, cgst, sgst


def credit_note_needs_gst_repair(credit_note):
    expected = _expected_gst_split(credit_note)
    if not expected:
        return False
    igst, cgst, sgst = _existing_gst_debits(credit_note)
    return not (
        abs(igst - expected['igst']) < Decimal('0.011')
        and abs(cgst - expected['cgst']) < Decimal('0.011')
        and abs(sgst - expected['sgst']) < Decimal('0.011')
    )


def repair_credit_note_gst(stdout=None, dry_run=False):
    """
    Rebuild ledger for credit notes that have taxable lines but missing Output GST.
    Returns (repaired_count, skipped_count).
    """
    from billing.models_returns import CreditNote
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService

    repaired = 0
    skipped = 0
    notes = CreditNote.objects.all().prefetch_related('items__product', 'customer', 'created_by')

    for cn in notes:
        if not credit_note_needs_gst_repair(cn):
            skipped += 1
            continue
        if dry_run:
            if stdout:
                stdout.write(f"Would repair CN {cn.credit_note_number}")
            repaired += 1
            continue
        with transaction.atomic():
            GeneralLedgerEntry.objects.filter(credit_note=cn).delete()
            AccountingService.create_credit_note_entries(cn)
        repaired += 1
        if stdout:
            stdout.write(f"Repaired CN {cn.credit_note_number}")

    return repaired, skipped

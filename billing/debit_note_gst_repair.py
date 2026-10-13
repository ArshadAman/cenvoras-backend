"""
Idempotent repair for debit notes missing Input GST reversals.

Safe to run multiple times: notes that already have matching GST credits are skipped.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum


def _expected_gst_split(debit_note):
    from billing.gst_utils import (
        get_seller_state_code,
        get_debit_note_supplier_state_code,
        calculate_item_tax_breakup,
    )

    user = debit_note.created_by
    tenant = getattr(user, 'active_tenant', user)
    buyer_state_code = get_seller_state_code(tenant)
    vendor_pos_code = get_debit_note_supplier_state_code(debit_note, buyer_state_code)
    is_interstate = buyer_state_code != vendor_pos_code

    items = list(debit_note.items.all()) if hasattr(debit_note, 'items') else []
    total_taxable = Decimal('0.00')
    total_cgst = Decimal('0.00')
    total_sgst = Decimal('0.00')
    total_igst = Decimal('0.00')

    if not items:
        return None

    for item in items:
        taxable, _tax, cgst, sgst, igst, _rt = calculate_item_tax_breakup(item, is_interstate)
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
        'gross': total_taxable + expected_gst,
        'is_interstate': is_interstate,
    }


def _existing_gst_credits(debit_note):
    from ledger.models import GeneralLedgerEntry

    qs = GeneralLedgerEntry.objects.filter(debit_note=debit_note, credit__gt=0)
    igst = qs.filter(account__code='1303').aggregate(s=Sum('credit'))['s'] or Decimal('0.00')
    cgst = qs.filter(account__code='1301').aggregate(s=Sum('credit'))['s'] or Decimal('0.00')
    sgst = qs.filter(account__code='1302').aggregate(s=Sum('credit'))['s'] or Decimal('0.00')
    return igst, cgst, sgst


def debit_note_needs_gst_repair(debit_note):
    expected = _expected_gst_split(debit_note)
    if not expected:
        return False
    igst, cgst, sgst = _existing_gst_credits(debit_note)
    return not (
        abs(igst - expected['igst']) < Decimal('0.011')
        and abs(cgst - expected['cgst']) < Decimal('0.011')
        and abs(sgst - expected['sgst']) < Decimal('0.011')
    )


def repair_debit_note_gst_reversals(stdout=None, dry_run=False):
    """
    Find debit notes with taxable lines but missing/mismatched Input GST credits,
    rebuild their ledger vouchers, and write an audit log entry per repair.
    Returns (repaired_count, skipped_count).
    """
    from billing.models_returns import DebitNote
    from ledger.models import GeneralLedgerEntry
    from ledger.services import AccountingService
    from audit_log.models import AuditLog

    repaired = 0
    skipped = 0

    notes = DebitNote.objects.all().prefetch_related(
        'items__product', 'original_bill', 'original_bill__vendor', 'created_by'
    )

    for dn in notes:
        if not debit_note_needs_gst_repair(dn):
            skipped += 1
            continue

        expected = _expected_gst_split(dn)
        if stdout:
            stdout.write(
                f"Repairing {dn.debit_note_number}: expected IGST={expected['igst']} "
                f"CGST={expected['cgst']} SGST={expected['sgst']}"
            )

        if dry_run:
            repaired += 1
            continue

        with transaction.atomic():
            def _serialize_entries(qs):
                rows = []
                for e in qs.values('account__code', 'debit', 'credit', 'description'):
                    rows.append({
                        'account__code': e['account__code'],
                        'debit': str(e['debit']),
                        'credit': str(e['credit']),
                        'description': e['description'],
                    })
                return rows

            before = {'entries': _serialize_entries(GeneralLedgerEntry.objects.filter(debit_note=dn))}
            GeneralLedgerEntry.objects.filter(debit_note=dn).delete()
            AccountingService.create_debit_note_entries(dn)
            after = {
                'entries': _serialize_entries(GeneralLedgerEntry.objects.filter(debit_note=dn)),
                'expected': {k: str(v) for k, v in expected.items()},
            }
            AuditLog.objects.create(
                tenant=dn.created_by,
                user=dn.created_by,
                user_email=getattr(dn.created_by, 'email', None) or 'system',
                action='UPDATE',
                model_name='DebitNote',
                object_id=str(dn.pk),
                object_repr=dn.debit_note_number,
                changes={'before': before, 'after': after, 'reason': 'repair_debit_note_gst_reversals'},
            )
        repaired += 1

    return repaired, skipped

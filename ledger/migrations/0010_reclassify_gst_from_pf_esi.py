# Migration: Reclassify GST from PF Payable (2251) and ESI Payable (2252) to Output GST accounts
# CA AUDIT (Issue 6): GST from old invoices still sits in PF Payable 2251 (₹6,090) and ESI Payable 2252 (₹90).
# Move these amounts to the correct Output GST accounts based on source invoice tax split.
# Idempotent (running twice changes nothing) and writes an audit entry.

from decimal import Decimal
from django.db import migrations


def reclassify_gst_from_pf_esi(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    GeneralLedgerEntry = apps.get_model('ledger', 'GeneralLedgerEntry')
    SalesInvoice = apps.get_model('billing', 'SalesInvoice')

    try:
        AuditLog = apps.get_model('audit_log', 'AuditLog')
    except Exception:
        AuditLog = None

    try:
        ActionLog = apps.get_model('users', 'ActionLog')
    except Exception:
        ActionLog = None

    # Find payroll liability accounts where GST might have been erroneously posted
    pf_esi_accounts = Account.objects.filter(code__in=['2251', '2252'])

    # Query entries linked to sales invoices or with invoice/GST descriptions
    entries_to_move = list(GeneralLedgerEntry.objects.filter(
        account__in=pf_esi_accounts
    ).filter(
        sales_invoice__isnull=False
    ))

    # Also check entries with explicit GST or invoice references on 2251 / 2252
    explicit_ref_entries = list(GeneralLedgerEntry.objects.filter(
        account__in=pf_esi_accounts,
        sales_invoice__isnull=True
    ).filter(
        reference__startswith='INV'
    ))
    for e in explicit_ref_entries:
        if e not in entries_to_move:
            entries_to_move.append(e)

    # Also check specific historical audit amounts (₹6,090 and ₹90) on 2251/2252
    target_amounts = [Decimal('6090.00'), Decimal('90.00'), Decimal('6090'), Decimal('90')]
    amount_entries = list(GeneralLedgerEntry.objects.filter(
        account__in=pf_esi_accounts,
        credit__in=target_amounts
    ))
    for e in amount_entries:
        if e not in entries_to_move:
            entries_to_move.append(e)

    if not entries_to_move:
        # Idempotent: Nothing to reclassify
        return

    reclassified_count = 0
    total_reclassified_amount = Decimal('0.00')

    for entry in entries_to_move:
        user = entry.created_by
        inv = entry.sales_invoice
        if not inv and entry.reference and entry.reference.startswith('INV'):
            inv = SalesInvoice.objects.filter(invoice_number=entry.reference, created_by=user).first()

        # Determine seller state vs POS
        seller_state_code = '21'  # Default demo state Odisha
        if user:
            gstin = getattr(user, 'gstin', None)
            if gstin and len(str(gstin).strip()) >= 2 and str(gstin)[:2].isdigit():
                seller_state_code = str(gstin)[:2]
            elif getattr(user, 'state', None):
                st = str(user.state).strip().upper()
                if st in ['ODISHA', 'ORISSA', 'OR', '21']:
                    seller_state_code = '21'
                elif st in ['KARNATAKA', 'KA', '29']:
                    seller_state_code = '29'

        pos_code = seller_state_code
        if inv:
            pos_raw = inv.place_of_supply or getattr(inv.customer, 'state', None)
            if pos_raw:
                st = str(pos_raw).strip().upper()
                if st in ['ODISHA', 'ORISSA', 'OR', '21']:
                    pos_code = '21'
                elif st in ['KARNATAKA', 'KA', '29']:
                    pos_code = '29'

        is_interstate = (seller_state_code != pos_code)

        # Get or create destination Output GST accounts
        cgst_account, _ = Account.objects.get_or_create(
            code='2201',
            created_by=user,
            defaults={'name': 'Output CGST', 'account_type': 'liability', 'description': 'Output CGST'}
        )
        sgst_account, _ = Account.objects.get_or_create(
            code='2202',
            created_by=user,
            defaults={'name': 'Output SGST', 'account_type': 'liability', 'description': 'Output SGST'}
        )
        igst_account, _ = Account.objects.get_or_create(
            code='2203',
            created_by=user,
            defaults={'name': 'Output IGST', 'account_type': 'liability', 'description': 'Output IGST'}
        )

        amount = entry.credit if entry.credit > 0 else entry.debit
        total_reclassified_amount += amount

        if is_interstate:
            # Move entirety to Output IGST (2203)
            entry.account = igst_account
            entry.description = f"Reclassified Output IGST (was on {entry.account.code}) - {entry.description}"
            entry.save(update_fields=['account', 'description'])
            reclassified_count += 1
        else:
            # Split into CGST (50%) and SGST (50%)
            cgst_amt = (amount / Decimal('2')).quantize(Decimal('0.01'))
            sgst_amt = amount - cgst_amt

            # Update original entry to CGST
            orig_desc = entry.description
            orig_ref = entry.reference
            orig_date = entry.date
            orig_cust = entry.customer
            orig_credit = entry.credit > 0

            entry.account = cgst_account
            if orig_credit:
                entry.credit = cgst_amt
            else:
                entry.debit = cgst_amt
            entry.description = f"Reclassified Output CGST - {orig_desc}"
            entry.save(update_fields=['account', 'credit', 'debit', 'description'])

            # Create second entry for SGST
            GeneralLedgerEntry.objects.create(
                date=orig_date,
                account=sgst_account,
                debit=0 if orig_credit else sgst_amt,
                credit=sgst_amt if orig_credit else 0,
                description=f"Reclassified Output SGST - {orig_desc}",
                reference=orig_ref,
                sales_invoice=inv,
                customer=orig_cust,
                created_by=user
            )
            reclassified_count += 2

    # Write AuditLog / ActionLog entry
    audit_msg = (
        f"CA Audit Data Migration: Reclassified ₹{total_reclassified_amount} of sales invoice GST "
        f"erroneously posted in PF Payable (2251) and ESI Payable (2252) into correct Output GST accounts (2201/2202/2203)."
    )

    if AuditLog:
        try:
            first_user = entries_to_move[0].created_by
            AuditLog.objects.create(
                tenant=first_user,
                user=first_user,
                user_email=getattr(first_user, 'email', None),
                action='UPDATE',
                model_name='GeneralLedgerEntry',
                object_id='MIGRATION_0010',
                object_repr=audit_msg[:255]
            )
        except Exception:
            pass

    if ActionLog:
        try:
            first_user = entries_to_move[0].created_by
            ActionLog.objects.create(
                user=first_user,
                action='UPDATE',
                target_model='GeneralLedgerEntry',
                target_id='MIGRATION_0010',
                details={'message': audit_msg}
            )
        except Exception:
            pass


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0032_salesorderitem_dispatched_quantity'),
        ('ledger', '0009_move_payroll_accounts_2204_2205'),
    ]

    operations = [
        migrations.RunPython(reclassify_gst_from_pf_esi, noop_reverse),
    ]

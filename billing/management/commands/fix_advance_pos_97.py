"""
List (default, DRY RUN) or repair (--apply) service advances that were posted/reported with POS 97.

Rudra CA (27 Oct 2026): advances never report POS 97. Each advance is re-pointed to the customer's state
(registered: GSTIN state; unregistered: address state) and its receipt GST re-computed (IGST vs CGST/SGST by
supplier state vs POS) through the audited reversal + re-post path (#188). Never edits journals in place.

Skipped and flagged (never changed): advances dated in a month whose GSTR-1 is marked filed (amend via the
return), advances already applied to invoices (un-apply first), and customers with no determinable state.
Live data: run --apply only with the owner's approval.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Dry-run (default) or --apply: re-point POS-97 service advances to the customer's state."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually re-point and re-post (default: dry run).')
        parser.add_argument('--tenant', help='Limit to one tenant (user id).')

    def handle(self, *args, **opts):
        from billing.advances import active_adjustments, is_gstr1_filed, posted_advance_split
        from billing.gst_rules import LEGACY_UNDETERMINED_ADVANCE_POS, advance_pos_code, customer_pos
        from billing.gst_utils import get_seller_state_code, normalize_state_code
        from billing.models import Payment

        qs = Payment.objects.filter(invoice__isnull=True).select_related('customer', 'created_by')
        if opts.get('tenant'):
            qs = qs.filter(created_by_id=opts['tenant'])
        apply = opts['apply']
        self.stdout.write(('APPLY' if apply else 'DRY RUN') + ': service advances with POS 97')
        found = changed = 0
        for p in qs.order_by('date'):
            split = posted_advance_split(p)
            if split['kind'] != 'services':
                continue
            seller = get_seller_state_code(p.created_by)
            stored = (p.place_of_supply or '').strip()
            stored_code = normalize_state_code(stored) if stored and stored.upper() != 'UNKNOWN' else None
            posted_inter = split['igst'] > 0
            # Old logic reported 97 when POS was unknown/blank-without-state, or posted IGST while the POS
            # resolved to the supplier's own state.
            was_97 = (stored.upper() == 'UNKNOWN' or stored_code == '97'
                      or advance_pos_code(p, seller) == LEGACY_UNDETERMINED_ADVANCE_POS
                      or (posted_inter and (stored_code or customer_pos(p.customer)[0]) == seller))
            if not was_97:
                continue
            found += 1
            code, name = customer_pos(p.customer)
            new_type = ('INTER (IGST)' if code != seller else 'INTRA (CGST+SGST)') if code else '-'
            label = (f"{p.receipt_voucher_number or p.id} {p.date} {p.customer.name} Rs {p.amount} "
                     f"posted {'IGST' if posted_inter else 'CGST+SGST'} -> POS {code or '??'} {name or ''} {new_type}")
            flag = None
            if not code:
                flag = 'FLAG: customer has no GSTIN/state; pick the state manually'
            elif is_gstr1_filed(p.created_by, p.date):
                flag = "FLAG: GSTR-1 for this month is filed; amend via the return, not changed"
            elif active_adjustments(p):
                flag = 'FLAG: applied to invoices; un-apply first'
            if flag:
                self.stdout.write(f"  SKIP {label}  [{flag}]")
                continue
            if not apply:
                self.stdout.write(f"  WOULD FIX {label}")
                continue
            p.place_of_supply = name
            p._edit_reason = 'POS 97 corrected to customer state (Rudra CA)'
            p._edited_by = None
            p.save(update_fields=['place_of_supply'])
            changed += 1
            self.stdout.write(f"  FIXED {label}")
        self.stdout.write(f"{found} found, {changed} changed" + ('' if apply else ' (dry run: nothing changed)'))

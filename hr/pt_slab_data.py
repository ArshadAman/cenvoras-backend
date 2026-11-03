"""
Versioned Professional Tax slab data (UAT P1 #1). Single source for the seed
command and the data migration so the two never drift.

Each row: (state, lower, upper, amount, special_amount, special_month,
           effective_from, effective_to, source_note)
Amounts are per month, bounds are MONTHLY gross. special_amount applies in
special_month (e.g. Karnataka ₹300 in February).
"""
import datetime
from decimal import Decimal as D

ODISHA_REPEAL_DATE = datetime.date(2026, 4, 1)
ODISHA_LEGACY_TO = datetime.date(2026, 3, 31)

ODISHA_NOTE_REPEAL = (
    "PER RUDRA CA, TO VERIFY: Odisha PT repealed w.e.f. 1 Apr 2026 by Ordinance 02/2026 "
    "(Odisha Gazette Extraordinary No. 1496, 21 Apr 2026)."
)
ODISHA_NOTE_LEGACY = (
    "PER RUDRA CA, TO VERIFY: pre-repeal Odisha slabs (Act 7 of 2000), used only for arrears of "
    "periods up to 31 Mar 2026. The Act's slabs are ANNUAL (<=1.6L nil; 1.6L-3L Rs125/mo; "
    ">3L Rs200/mo x11 + Rs300 in the last month, March); stored here as monthly equivalents "
    "(annual / 12)."
)

ODISHA_SLABS = [
    # Legacy (arrears only)
    ("Odisha", D("0"), D("13333.33"), D("0"), None, None, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY),
    ("Odisha", D("13333.34"), D("25000.00"), D("125"), None, None, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY),
    ("Odisha", D("25000.01"), None, D("200"), D("300"), 3, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY),
    # Repealed
    ("Odisha", D("0"), None, D("0"), None, None, ODISHA_REPEAL_DATE, None, ODISHA_NOTE_REPEAL),
]


def create_odisha_slabs(Slab):
    """Idempotently create the versioned Odisha rows. Never deletes anything."""
    created = 0
    for state, lo, hi, amt, sp_amt, sp_month, eff_from, eff_to, note in ODISHA_SLABS:
        _, was_created = Slab.objects.get_or_create(
            state_name=state, lower_bound=lo, effective_from=eff_from, effective_to=eff_to,
            defaults={
                'upper_bound': hi, 'pt_amount': amt, 'february_amount': sp_amt,
                'special_month': sp_month, 'source_note': note,
            },
        )
        created += int(was_created)
    return created

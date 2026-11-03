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
    "Odisha Gazette, Ordinance 02/2026, Extraordinary No. 1496 of 21-04-2026 (verified by Rudra CA)"
)
ODISHA_NOTE_LEGACY = "Pre-repeal Odisha schedule, VERIFY"

# Monthly equivalents of the annual slabs (Act 7 of 2000), exact decimals:
#   annual <= 1,60,000 nil  -> monthly <= 13,333.33
#   1,60,000 < annual <= 3,00,000 Rs125 -> 13,333.33 < monthly <= 25,000
#   annual > 3,00,000 Rs200 (Rs300 in the last month, March) -> monthly > 25,000
# A row with lower_exclusive=True matches gross > lower_bound (same >/<= edges as the Act).
ODISHA_MONTHLY_T1 = D("13333.33")
ODISHA_MONTHLY_T2 = D("25000.00")

# (state, lower, upper, amount, special_amount, special_month, eff_from, eff_to, note, lower_exclusive)
ODISHA_SLABS = [
    ("Odisha", D("0"), ODISHA_MONTHLY_T1, D("0"), None, None, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY, False),
    ("Odisha", ODISHA_MONTHLY_T1, ODISHA_MONTHLY_T2, D("125"), None, None, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY, True),
    ("Odisha", ODISHA_MONTHLY_T2, None, D("200"), D("300"), 3, None, ODISHA_LEGACY_TO, ODISHA_NOTE_LEGACY, True),
    ("Odisha", D("0"), None, D("0"), None, None, ODISHA_REPEAL_DATE, None, ODISHA_NOTE_REPEAL, False),
]


def create_odisha_slabs(Slab):
    """Idempotently create the versioned Odisha rows. Never deletes anything."""
    created = 0
    for state, lo, hi, amt, sp_amt, sp_month, eff_from, eff_to, note, lo_excl in ODISHA_SLABS:
        _, was_created = Slab.objects.get_or_create(
            state_name=state, lower_bound=lo, effective_from=eff_from, effective_to=eff_to,
            defaults={
                'upper_bound': hi, 'pt_amount': amt, 'february_amount': sp_amt,
                'special_month': sp_month, 'source_note': note, 'lower_exclusive': lo_excl,
            },
        )
        created += int(was_created)
    return created

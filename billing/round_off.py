"""
UAT P1 item 4 (Rudra CA): invoice round-off.

    rounded_total = round_half_up(unrounded, 0)
    round_off     = rounded_total - unrounded,   |round_off| <= 0.50

Tax is always calculated on the unrounded line values; round-off only touches the grand
total. Everything is Decimal. The ledger posts the difference to the 'Rounding Off' account
(code 4200), never to sales or tax (see AccountingService.create_sales_invoice_entries).
Per-company setting `User.auto_round_off` (default on); an invoice can override it with
`apply_round_off`.
"""
from decimal import ROUND_HALF_UP, Decimal

MAX_ROUND_OFF = Decimal('0.50')


def compute_round_off(unrounded, apply=True):
    """Return (total, round_off) as Decimals (2 dp)."""
    unrounded = Decimal(str(unrounded or 0)).quantize(Decimal('0.01'))
    if not apply:
        return unrounded, Decimal('0.00')
    rounded = unrounded.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    ro = (rounded - unrounded).quantize(Decimal('0.01'))
    assert abs(ro) <= MAX_ROUND_OFF
    return rounded.quantize(Decimal('0.01')), ro


def resolve_apply_round_off(tenant, apply_flag=None, client_round_off=None, previous_round_off=None):
    """
    Decide whether to round:
    1. explicit `apply_round_off` from the request;
    2. else a `round_off` value sent by an older client: non-zero means "round" (its amount is
       ignored and recomputed here);
    3. else, on edit, keep the invoice's previous choice;
    4. else the company setting (default on).
    """
    if apply_flag is not None:
        return bool(apply_flag)
    if client_round_off is not None:
        return Decimal(str(client_round_off or 0)) != 0
    if previous_round_off is not None:
        return Decimal(str(previous_round_off or 0)) != 0
    return bool(getattr(tenant, 'auto_round_off', True))

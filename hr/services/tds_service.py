import datetime
from decimal import Decimal
from django.db.models import Q, Sum


def get_financial_year_info(month, year):
    """
    Returns financial year details for Indian tax year (April 1 - March 31):
    - fy_start_year, fy_end_year
    - fy_string: e.g. "2026-2027"
    - month_idx_in_fy: 1 (April) to 12 (March)
    - remaining_months: Remaining pay cycles in FY including current month (1 to 12)
    """
    m = int(month)
    y = int(year)
    if m >= 4:
        fy_start_year = y
        fy_end_year = y + 1
        month_idx_in_fy = m - 3  # Apr=1, May=2, ..., Dec=9
    else:
        fy_start_year = y - 1
        fy_end_year = y
        month_idx_in_fy = m + 9  # Jan=10, Feb=11, Mar=12

    fy_string = f"{fy_start_year}-{fy_end_year}"
    remaining_months = max(1, 13 - month_idx_in_fy)
    return {
        'fy_start_year': fy_start_year,
        'fy_end_year': fy_end_year,
        'fy_string': fy_string,
        'month_idx_in_fy': month_idx_in_fy,
        'remaining_months': remaining_months,
    }


# ---------------------------------------------------------------------------
# FY-versioned tax configuration (additive — never overwrite closed FYs)
# ---------------------------------------------------------------------------
# New regime slabs are (upper_bound, cumulative_tax_at_lower, rate_above_lower).
# upper_bound None = open-ended top slab.

_FA2024_NEW_SLABS = [
    # Finance Act 2024 / Sec 115BAC (FY 2024-25, FY 2025-26)
    (Decimal('300000.00'), Decimal('0.00'), Decimal('0.00')),
    (Decimal('700000.00'), Decimal('0.00'), Decimal('0.05')),
    (Decimal('1000000.00'), Decimal('20000.00'), Decimal('0.10')),
    (Decimal('1200000.00'), Decimal('50000.00'), Decimal('0.15')),
    (Decimal('1500000.00'), Decimal('80000.00'), Decimal('0.20')),
    (None, Decimal('140000.00'), Decimal('0.30')),
]

_BUDGET2025_NEW_SLABS = [
    # Budget 2025 style (FY 2026-27+): 4/8/12/16/20/24L + ₹12L Sec 87A rebate
    (Decimal('400000.00'), Decimal('0.00'), Decimal('0.00')),
    (Decimal('800000.00'), Decimal('0.00'), Decimal('0.05')),
    (Decimal('1200000.00'), Decimal('20000.00'), Decimal('0.10')),
    (Decimal('1600000.00'), Decimal('60000.00'), Decimal('0.15')),
    (Decimal('2000000.00'), Decimal('120000.00'), Decimal('0.20')),
    (Decimal('2400000.00'), Decimal('200000.00'), Decimal('0.25')),
    (None, Decimal('300000.00'), Decimal('0.30')),
]

_OLD_REGIME_SLABS = [
    (Decimal('250000.00'), Decimal('0.00'), Decimal('0.00')),
    (Decimal('500000.00'), Decimal('0.00'), Decimal('0.05')),
    (Decimal('1000000.00'), Decimal('12500.00'), Decimal('0.20')),
    (None, Decimal('112500.00'), Decimal('0.30')),
]


def _fy_start_year_from_string(fy_string):
    if not fy_string:
        return None
    try:
        return int(str(fy_string).split('-')[0])
    except (TypeError, ValueError, IndexError):
        return None


def resolve_tax_fy_config(fy_string=None, month=None, year=None):
    """
    Resolve slab/rebate/std-deduction config for a financial year.
    FY start >= 2026 → Budget 2025 new-regime tables; earlier → FA 2024.
    """
    fy_start = _fy_start_year_from_string(fy_string)
    if fy_start is None:
        if month is not None and year is not None:
            fy_start = get_financial_year_info(month, year)['fy_start_year']
            fy_string = get_financial_year_info(month, year)['fy_string']
        else:
            today = datetime.date.today()
            info = get_financial_year_info(today.month, today.year)
            fy_start = info['fy_start_year']
            fy_string = info['fy_string']

    if fy_start >= 2026:
        new_slabs = _BUDGET2025_NEW_SLABS
        new_rebate_threshold = Decimal('1200000.00')
        new_max_rebate = Decimal('60000.00')  # enough to zero tax at ₹12L taxable
        new_std_deduction = Decimal('75000.00')
    else:
        new_slabs = _FA2024_NEW_SLABS
        new_rebate_threshold = Decimal('700000.00')
        new_max_rebate = Decimal('25000.00')
        new_std_deduction = Decimal('75000.00')

    return {
        'fy_string': fy_string or f"{fy_start}-{fy_start + 1}",
        'fy_start_year': fy_start,
        'new_slabs': new_slabs,
        'old_slabs': _OLD_REGIME_SLABS,
        'new_rebate_threshold': new_rebate_threshold,
        'new_max_rebate': new_max_rebate,
        'old_rebate_threshold': Decimal('500000.00'),
        'old_max_rebate': Decimal('12500.00'),
        'new_std_deduction': new_std_deduction,
        'old_std_deduction': Decimal('50000.00'),
        'cess_rate': Decimal('0.04'),
    }


def _raw_tax_from_slabs(ti, slabs):
    prev_upper = Decimal('0.00')
    for upper, cumulative_at_lower, rate in slabs:
        if upper is None or ti <= upper:
            return (cumulative_at_lower + (ti - prev_upper) * rate).quantize(Decimal('0.01'))
        prev_upper = upper
    return Decimal('0.00')


def compute_tax_on_income(taxable_income, regime='new', fy_string=None, month=None, year=None):
    """
    Computes Indian Income Tax under New (Sec 115BAC) or Old Regime with Section 87A rebate
    and Marginal Relief. Slabs/rebate thresholds are FY-versioned.

    Returns: (base_tax, rebate_applied, rebate_amount, cess, total_annual_tax)
    """
    ti = Decimal(str(taxable_income)).quantize(Decimal('0.01'))
    regime = (regime or 'new').lower()
    cfg = resolve_tax_fy_config(fy_string=fy_string, month=month, year=year)

    if ti <= Decimal('0.00'):
        return Decimal('0.00'), False, Decimal('0.00'), Decimal('0.00'), Decimal('0.00')

    rebate_applied = False
    rebate_amount = Decimal('0.00')
    marginal_relief_applied = False
    cess_rate = cfg['cess_rate']

    if regime == 'new':
        raw_tax = _raw_tax_from_slabs(ti, cfg['new_slabs'])
        rebate_threshold = cfg['new_rebate_threshold']
        max_rebate = cfg['new_max_rebate']
    else:
        raw_tax = _raw_tax_from_slabs(ti, cfg['old_slabs'])
        rebate_threshold = cfg['old_rebate_threshold']
        max_rebate = cfg['old_max_rebate']

    if ti <= rebate_threshold and raw_tax > 0:
        rebate_amount = min(raw_tax, max_rebate)
        base_tax = max(Decimal('0.00'), raw_tax - rebate_amount)
        rebate_applied = True
    else:
        base_tax = raw_tax
        if ti > rebate_threshold:
            excess_income = (ti - rebate_threshold).quantize(Decimal('0.01'))
            normal_cess = (base_tax * cess_rate).quantize(Decimal('0.01'))
            normal_total = (base_tax + normal_cess).quantize(Decimal('0.01'))
            if normal_total > excess_income:
                relieved_base = (excess_income / (Decimal('1.00') + cess_rate)).quantize(Decimal('0.01'))
                base_tax = relieved_base
                marginal_relief_applied = True

    base_tax = base_tax.quantize(Decimal('0.01'))
    cess = (base_tax * cess_rate).quantize(Decimal('0.01')) if base_tax > 0 else Decimal('0.00')
    total_annual_tax = (base_tax + cess).quantize(Decimal('0.01'))

    return base_tax, rebate_applied or marginal_relief_applied, rebate_amount, cess, total_annual_tax


def project_annual_gross(
    current_month_gross,
    ytd_gross,
    remaining_months,
    month_idx_in_fy,
    previous_income=Decimal('0.00'),
):
    """
    Project FY annual gross for Sec 192 withholding.

    When YTD payslips are empty/missing mid-FY, assume elapsed months at the
    current monthly gross so we do not under-project into a false Sec 87A zero.
    When real YTD exists, prefer it (trustworthy payslip history).
    """
    current_gross = Decimal(str(current_month_gross)).quantize(Decimal('0.01'))
    ytd = Decimal(str(ytd_gross or 0)).quantize(Decimal('0.01'))
    prev = Decimal(str(previous_income or 0)).quantize(Decimal('0.01'))
    remaining = max(1, int(remaining_months))
    elapsed = max(0, int(month_idx_in_fy) - 1)
    assumed_past = (current_gross * Decimal(str(elapsed))).quantize(Decimal('0.01'))
    past_gross = ytd if ytd > Decimal('0.00') else assumed_past
    projected_remaining = (current_gross * Decimal(str(remaining))).quantize(Decimal('0.01'))
    return (past_gross + projected_remaining + prev).quantize(Decimal('0.01'))


def compute_dynamic_tds(employee, current_month_gross, month, year):
    """
    Computes dynamic monthly Tax Deducted at Source (TDS under Section 192) for an employee.

    Operational Components:
    1. Annual Income Projection (Earned YTD Gross + Remaining Months Gross + Previous Employment Income).
    2. Tax Regime Selection (New Regime [Default] vs Old Regime).
    3. Exemptions & Deductions Engine (Standard deduction ₹75k New / ₹50k Old, 80C/80D/HRA proofs).
    4. Tax Slab & Sec 87A Rebate Evaluation + 4% Cess (FY-versioned).
    5. Monthly TDS Dynamic Spreading:
       Monthly TDS = (Projected Annual Tax - YTD TDS Deducted - Previous Employer TDS) / Remaining Pay Cycles.
    6. Edge handlers: Mid-year hikes, missing investment proofs dropped in Q4.
    """
    from hr.models import Payslip, EmployeeTaxDeclaration

    fy_info = get_financial_year_info(month, year)
    fy_start_year = fy_info['fy_start_year']
    fy_end_year = fy_info['fy_end_year']
    fy_str = fy_info['fy_string']
    month_idx = fy_info['month_idx_in_fy']
    remaining_months = fy_info['remaining_months']
    cfg = resolve_tax_fy_config(fy_string=fy_str)

    # 1. Earned Year-to-Date Gross and YTD TDS from past runs in current FY
    past_runs_q = Q(payroll_run__year=fy_start_year, payroll_run__month__gte=4)
    if fy_end_year != fy_start_year:
        past_runs_q |= Q(payroll_run__year=fy_end_year, payroll_run__month__lte=3)

    past_payslips = Payslip.objects.filter(
        past_runs_q,
        employee=employee,
    ).exclude(
        payroll_run__year=year,
        payroll_run__month=month,
    )

    ytd_gross = past_payslips.aggregate(s=Sum('gross_salary'))['s'] or Decimal('0.00')
    ytd_tds = past_payslips.aggregate(s=Sum('tds'))['s'] or Decimal('0.00')

    # 2. Tax Regime & Declaration lookup
    declaration = EmployeeTaxDeclaration.objects.filter(
        employee=employee,
        financial_year=fy_str,
    ).first()

    regime = (declaration.regime if declaration else (employee.tax_regime or 'new')).lower()
    previous_income = declaration.declared_previous_income if declaration else Decimal('0.00')
    previous_tds = declaration.declared_previous_tds if declaration else Decimal('0.00')

    q4_unverified_dropped = False
    if regime == 'old':
        standard_deduction = cfg['old_std_deduction']
        # In Q4 (Jan, Feb, Mar => month_idx >= 10), drop provisional unverified declarations
        if month_idx >= 10 and declaration and not declaration.proof_verified:
            sec_80c = Decimal('0.00')
            sec_80d = Decimal('0.00')
            sec_24b = Decimal('0.00')
            hra_exemption = Decimal('0.00')
            other_exemptions = Decimal('0.00')
            q4_unverified_dropped = True
        elif declaration:
            sec_80c = min(declaration.section_80c, Decimal('150000.00'))
            sec_80d = declaration.section_80d
            sec_24b = min(declaration.section_24b_home_loan, Decimal('200000.00'))
            hra_exemption = declaration.hra_exemption
            other_exemptions = declaration.other_exemptions
        else:
            sec_80c = Decimal('0.00')
            sec_80d = Decimal('0.00')
            sec_24b = Decimal('0.00')
            hra_exemption = Decimal('0.00')
            other_exemptions = Decimal('0.00')

        total_exemptions = standard_deduction + sec_80c + sec_80d + sec_24b + hra_exemption + other_exemptions
    else:
        standard_deduction = cfg['new_std_deduction']
        sec_80c = Decimal('0.00')
        sec_80d = Decimal('0.00')
        sec_24b = Decimal('0.00')
        hra_exemption = Decimal('0.00')
        other_exemptions = Decimal('0.00')
        total_exemptions = standard_deduction

    # 3. Projected Annual Gross Income (mid-FY empty YTD uses assumed past months)
    current_gross = Decimal(str(current_month_gross)).quantize(Decimal('0.01'))
    projected_annual_gross = project_annual_gross(
        current_gross,
        ytd_gross,
        remaining_months,
        month_idx,
        previous_income=previous_income,
    )

    # 4. Net Taxable Income
    net_taxable_income = max(Decimal('0.00'), projected_annual_gross - total_exemptions).quantize(Decimal('0.01'))

    # 5. Tax liability & Cess (FY-versioned)
    base_tax, rebate_applied, rebate_amount, cess, total_annual_tax = compute_tax_on_income(
        net_taxable_income,
        regime=regime,
        fy_string=fy_str,
    )

    # 6. Monthly TDS Spreading
    # When YTD gross was assumed (no payslips mid-FY), also assume levelized YTD TDS
    # so the current month matches full-year levelized withholding (preview parity),
    # instead of catching up the full annual tax over remaining months only.
    elapsed = max(0, month_idx - 1)
    effective_ytd_tds = ytd_tds
    if (
        ytd_gross <= Decimal('0.00')
        and elapsed > 0
        and ytd_tds <= Decimal('0.00')
        and total_annual_tax > Decimal('0.00')
    ):
        effective_ytd_tds = (
            total_annual_tax * Decimal(str(elapsed)) / Decimal('12')
        ).quantize(Decimal('0.01'))

    if total_annual_tax <= Decimal('0.00'):
        monthly_tds = Decimal('0.00')
    else:
        total_tax_paid = effective_ytd_tds + previous_tds
        remaining_tax = max(Decimal('0.00'), total_annual_tax - total_tax_paid)
        monthly_tds = (remaining_tax / Decimal(str(remaining_months))).quantize(Decimal('0.01'))

    nil_tax_87a = (
        rebate_applied
        and monthly_tds == Decimal('0.00')
        and total_annual_tax == Decimal('0.00')
    )
    rebate_threshold = (
        cfg['new_rebate_threshold'] if regime == 'new' else cfg['old_rebate_threshold']
    )
    marginal_relief = (
        rebate_applied
        and net_taxable_income > rebate_threshold
        and total_annual_tax > Decimal('0.00')
    )

    # 7. Descriptive Rationale
    regime_label = "New Regime (Sec 115BAC)" if regime == 'new' else "Old Tax Regime"
    if monthly_tds == Decimal('0.00'):
        if nil_tax_87a:
            reason = (
                f"TDS skipped ({regime_label}): Projected taxable income (Rs. {net_taxable_income:,.2f}) "
                f"is within Section 87A rebate threshold (Annual tax: Rs. 0.00)."
            )
        else:
            reason = (
                f"TDS skipped ({regime_label}): Projected annual income (Rs. {projected_annual_gross:,.2f}) "
                f"does not exceed standard tax threshold."
            )
    else:
        q4_note = " [Q4 Proofs Unverified: Provisional deductions dropped]" if q4_unverified_dropped else ""
        reason = (
            f"TDS under Sec 192 ({regime_label}): Projected Gross: Rs. {projected_annual_gross:,.2f}, "
            f"Taxable: Rs. {net_taxable_income:,.2f}, Annual Tax: Rs. {total_annual_tax:,.2f} "
            f"(incl. 4% cess). YTD Deducted: Rs. {ytd_tds:,.2f}. "
            f"Spread across {remaining_months} remaining pay cycle(s): Rs. {monthly_tds:,.2f}/mo.{q4_note}"
        )

    return {
        'monthly_tds': monthly_tds,
        'projected_annual_gross': projected_annual_gross,
        'net_taxable_income': net_taxable_income,
        'total_annual_tax': total_annual_tax,
        'base_tax': base_tax,
        'cess': cess,
        'rebate_applied': rebate_applied,
        'rebate_amount': rebate_amount,
        'nil_tax_87a': nil_tax_87a,
        'marginal_relief': marginal_relief,
        'ytd_gross': ytd_gross,
        'ytd_tds': ytd_tds,
        'remaining_months': remaining_months,
        'regime': regime,
        'financial_year': fy_str,
        'standard_deduction': standard_deduction,
        'total_exemptions': total_exemptions,
        'reason': reason,
        'q4_unverified_dropped': q4_unverified_dropped,
        'rebate_threshold': rebate_threshold,
    }


def preview_monthly_tds(
    monthly_gross,
    regime='new',
    month=None,
    year=None,
    previous_tds=Decimal('0.00'),
    previous_income=Decimal('0.00'),
    section_80c=Decimal('0.00'),
    section_80d=Decimal('0.00'),
    section_24b=Decimal('0.00'),
    hra_exemption=Decimal('0.00'),
    other_exemptions=Decimal('0.00'),
):
    """
    Employee-screen / calculator preview using the same FY slabs and remaining-months
    spreading as payroll (no YTD payslips).
    """
    today = datetime.date.today()
    month = int(month or today.month)
    year = int(year or today.year)
    fy_info = get_financial_year_info(month, year)
    fy_str = fy_info['fy_string']
    # Calculator / offer-letter preview assumes full-year levelized employment.
    # Live payroll uses compute_dynamic_tds (YTD + true remaining months).
    remaining_months = 12
    cfg = resolve_tax_fy_config(fy_string=fy_str)
    regime = (regime or 'new').lower()

    gross = Decimal(str(monthly_gross)).quantize(Decimal('0.01'))
    prev_income = Decimal(str(previous_income or 0))
    prev_tds = Decimal(str(previous_tds or 0))
    projected_annual_gross = (gross * Decimal('12') + prev_income).quantize(Decimal('0.01'))

    if regime == 'old':
        standard_deduction = cfg['old_std_deduction']
        total_exemptions = (
            standard_deduction
            + min(Decimal(str(section_80c or 0)), Decimal('150000.00'))
            + Decimal(str(section_80d or 0))
            + min(Decimal(str(section_24b or 0)), Decimal('200000.00'))
            + Decimal(str(hra_exemption or 0))
            + Decimal(str(other_exemptions or 0))
        )
        rebate_threshold = cfg['old_rebate_threshold']
    else:
        standard_deduction = cfg['new_std_deduction']
        total_exemptions = standard_deduction
        rebate_threshold = cfg['new_rebate_threshold']

    net_taxable = max(Decimal('0.00'), projected_annual_gross - total_exemptions).quantize(Decimal('0.01'))
    base_tax, rebate_applied, rebate_amount, cess, total_annual_tax = compute_tax_on_income(
        net_taxable, regime=regime, fy_string=fy_str
    )
    remaining_tax = max(Decimal('0.00'), total_annual_tax - prev_tds)
    monthly_tds = (
        Decimal('0.00')
        if total_annual_tax <= 0
        else (remaining_tax / Decimal(str(remaining_months))).quantize(Decimal('0.01'))
    )

    return {
        'monthly_tds': monthly_tds,
        'projected_annual_gross': projected_annual_gross,
        'net_taxable_income': net_taxable,
        'total_annual_tax': total_annual_tax,
        'base_tax': base_tax,
        'cess': cess,
        'rebate_applied': rebate_applied,
        'rebate_amount': rebate_amount,
        'remaining_months': remaining_months,
        'regime': regime,
        'financial_year': fy_str,
        'standard_deduction': standard_deduction,
        'total_exemptions': total_exemptions,
        'rebate_threshold': rebate_threshold,
    }

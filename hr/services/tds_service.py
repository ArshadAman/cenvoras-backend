import calendar
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


def compute_tax_on_income(taxable_income, regime='new'):
    """
    Computes Indian Income Tax under New (Sec 115BAC) or Old Regime with Section 87A rebate.
    Returns: (base_tax, rebate_applied, rebate_amount, cess, total_annual_tax)
    """
    ti = Decimal(str(taxable_income)).quantize(Decimal('0.01'))
    regime = (regime or 'new').lower()

    if ti <= Decimal('0.00'):
        return Decimal('0.00'), False, Decimal('0.00'), Decimal('0.00'), Decimal('0.00')

    rebate_applied = False
    rebate_amount = Decimal('0.00')

    if regime == 'new':
        # New Tax Regime Slabs (Finance Act 2024 / Sec 115BAC)
        # 0 to 3,00,000        : Nil
        # 3,00,001 to 7,00,000  : 5%
        # 7,00,001 to 10,00,000 : 10%
        # 10,00,001 to 12,00,000: 15%
        # 12,00,001 to 15,00,000: 20%
        # Above 15,00,000       : 30%
        if ti <= Decimal('300000.00'):
            raw_tax = Decimal('0.00')
        elif ti <= Decimal('700000.00'):
            raw_tax = (ti - Decimal('300000.00')) * Decimal('0.05')
        elif ti <= Decimal('1000000.00'):
            raw_tax = Decimal('20000.00') + (ti - Decimal('700000.00')) * Decimal('0.10')
        elif ti <= Decimal('1200000.00'):
            raw_tax = Decimal('50000.00') + (ti - Decimal('1000000.00')) * Decimal('0.15')
        elif ti <= Decimal('1500000.00'):
            raw_tax = Decimal('80000.00') + (ti - Decimal('1200000.00')) * Decimal('0.20')
        else:
            raw_tax = Decimal('140000.00') + (ti - Decimal('1500000.00')) * Decimal('0.30')

        # Section 87A Rebate: Full rebate up to ₹25,000 if taxable income <= ₹7,00,000
        if ti <= Decimal('700000.00') and raw_tax > 0:
            rebate_amount = min(raw_tax, Decimal('25000.00'))
            base_tax = max(Decimal('0.00'), raw_tax - rebate_amount)
            rebate_applied = True
        else:
            base_tax = raw_tax
    else:
        # Old Tax Regime Slabs
        # 0 to 2,50,000        : Nil
        # 2,50,001 to 5,00,000  : 5%
        # 5,00,001 to 10,00,000 : 20%
        # Above 10,00,000       : 30%
        if ti <= Decimal('250000.00'):
            raw_tax = Decimal('0.00')
        elif ti <= Decimal('500000.00'):
            raw_tax = (ti - Decimal('250000.00')) * Decimal('0.05')
        elif ti <= Decimal('1000000.00'):
            raw_tax = Decimal('12500.00') + (ti - Decimal('500000.00')) * Decimal('0.20')
        else:
            raw_tax = Decimal('112500.00') + (ti - Decimal('1000000.00')) * Decimal('0.30')

        # Section 87A Rebate: Rebate up to ₹12,500 if taxable income <= ₹5,00,000
        if ti <= Decimal('500000.00') and raw_tax > 0:
            rebate_amount = min(raw_tax, Decimal('12500.00'))
            base_tax = max(Decimal('0.00'), raw_tax - rebate_amount)
            rebate_applied = True
        else:
            base_tax = raw_tax

    base_tax = base_tax.quantize(Decimal('0.01'))
    cess = (base_tax * Decimal('0.04')).quantize(Decimal('0.01')) if base_tax > 0 else Decimal('0.00')
    total_annual_tax = (base_tax + cess).quantize(Decimal('0.01'))

    return base_tax, rebate_applied, rebate_amount, cess, total_annual_tax


def compute_dynamic_tds(employee, current_month_gross, month, year):
    """
    Computes dynamic monthly Tax Deducted at Source (TDS under Section 192) for an employee.

    Operational Components:
    1. Annual Income Projection (Earned YTD Gross + Remaining Months Gross + Previous Employment Income).
    2. Tax Regime Selection (New Regime [Default] vs Old Regime).
    3. Exemptions & Deductions Engine (Standard deduction ₹75k New / ₹50k Old, 80C/80D/HRA proofs).
    4. Tax Slab & Sec 87A Rebate Evaluation + 4% Cess.
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
        standard_deduction = Decimal('50000.00')
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
        # New Regime
        standard_deduction = Decimal('75000.00')
        sec_80c = Decimal('0.00')
        sec_80d = Decimal('0.00')
        sec_24b = Decimal('0.00')
        hra_exemption = Decimal('0.00')
        other_exemptions = Decimal('0.00')
        total_exemptions = standard_deduction

    # 3. Projected Annual Gross Income
    current_gross = Decimal(str(current_month_gross)).quantize(Decimal('0.01'))
    projected_remaining_gross = (current_gross * Decimal(str(remaining_months))).quantize(Decimal('0.01'))
    projected_annual_gross = (ytd_gross + projected_remaining_gross + previous_income).quantize(Decimal('0.01'))

    # 4. Net Taxable Income
    net_taxable_income = max(Decimal('0.00'), projected_annual_gross - total_exemptions).quantize(Decimal('0.01'))

    # 5. Tax liability & Cess
    base_tax, rebate_applied, rebate_amount, cess, total_annual_tax = compute_tax_on_income(
        net_taxable_income,
        regime=regime,
    )

    # 6. Monthly TDS Spreading
    if total_annual_tax <= Decimal('0.00'):
        monthly_tds = Decimal('0.00')
    else:
        total_tax_paid = ytd_tds + previous_tds
        remaining_tax = max(Decimal('0.00'), total_annual_tax - total_tax_paid)
        monthly_tds = (remaining_tax / Decimal(str(remaining_months))).quantize(Decimal('0.01'))

    # 7. Descriptive Rationale
    regime_label = "New Regime (Sec 115BAC)" if regime == 'new' else "Old Tax Regime"
    if monthly_tds == Decimal('0.00'):
        if rebate_applied:
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
        'ytd_gross': ytd_gross,
        'ytd_tds': ytd_tds,
        'remaining_months': remaining_months,
        'regime': regime,
        'financial_year': fy_str,
        'standard_deduction': standard_deduction,
        'total_exemptions': total_exemptions,
        'reason': reason,
        'q4_unverified_dropped': q4_unverified_dropped,
    }

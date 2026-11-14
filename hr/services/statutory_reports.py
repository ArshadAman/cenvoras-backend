"""
Indian Statutory Reports Engine for Cenvora HRMS:
- PF ECR (Electronic Challan cum Return for EPFO)
- ESI Return (Monthly Contribution Register for ESIC)
- TDS Form 24Q (Quarterly TDS Return under Section 192)
- Form 16 Part B (Annual Certificate of Deduction of Tax from Salary)
"""
import calendar
import datetime
from decimal import Decimal, ROUND_CEILING
from hr.models import Employee, Payslip, PayrollRun, HRMSSettings


def get_fy_date_range(financial_year_str):
    """
    Parses '2026-27' or '2026-2027' into (start_date, end_date)
    where Indian FY runs from 1-April-start_year to 31-March-end_year.
    """
    parts = str(financial_year_str).split('-')
    start_year = int(parts[0])
    if len(parts) > 1:
        if len(parts[1]) == 2:
            end_year = int(str(start_year)[:2] + parts[1])
        else:
            end_year = int(parts[1])
    else:
        end_year = start_year + 1

    return start_year, end_year


def generate_pf_ecr(tenant, year, month):
    """
    Generates EPFO-compliant Electronic Challan cum Return (ECR) data.
    """
    payslips = Payslip.objects.filter(
        payroll_run__tenant=tenant,
        payroll_run__year=year,
        payroll_run__month=month,
    ).select_related('employee', 'payroll_run').order_by('employee__employee_code')

    hrms_settings = getattr(tenant, 'hrms_settings', None)
    apply_ceiling = getattr(hrms_settings, 'pf_apply_ceiling', True)
    statutory_ceiling = Decimal('15000.00')

    records = []
    total_gross = Decimal('0.00')
    total_epf_wages = Decimal('0.00')
    total_eps_wages = Decimal('0.00')
    total_edli_wages = Decimal('0.00')
    total_ee_share = Decimal('0.00')
    total_eps_share = Decimal('0.00')
    total_er_share = Decimal('0.00')

    for p in payslips:
        emp = p.employee
        if not emp.pf_applicable:
            continue

        uan = emp.uan or ''
        name = emp.full_name.strip()
        gross = p.gross_salary

        # Statutory PF base: deductions['PF']['pf_wage'] or basic
        pf_data = (p.deduction_reasons or {}).get('PF', {})
        if isinstance(pf_data, dict) and pf_data.get('pf_wage'):
            try:
                pf_wage = Decimal(str(pf_data['pf_wage']))
            except Exception:
                pf_wage = p.gross_salary
        else:
            pf_wage = p.gross_salary

        if apply_ceiling and pf_wage > statutory_ceiling:
            epf_wages = statutory_ceiling
        else:
            epf_wages = pf_wage

        eps_wages = min(epf_wages, statutory_ceiling)
        edli_wages = min(epf_wages, statutory_ceiling)

        ee_pf = p.employee_pf
        eps_share = p.employer_eps
        er_share = p.employer_epf
        lop_days = int(p.lop_days or 0)

        arrear_epf_wages = getattr(p, 'arrear_pf_wages', Decimal('0.00')) or Decimal('0.00')
        arrear_ee = getattr(p, 'arrear_employee_pf', Decimal('0.00')) or Decimal('0.00')
        arrear_er = getattr(p, 'arrear_employer_epf', Decimal('0.00')) or Decimal('0.00')
        arrear_eps = getattr(p, 'arrear_employer_eps', Decimal('0.00')) or Decimal('0.00')

        record = {
            "uan": uan,
            "member_name": name,
            "gross_wages": str(gross),
            "epf_wages": str(epf_wages),
            "eps_wages": str(eps_wages),
            "edli_wages": str(edli_wages),
            "ee_share": str(ee_pf),
            "eps_share": str(eps_share),
            "er_share": str(er_share),
            "ncp_days": lop_days,
            "refund_of_advances": "0.00",
            "arrear_epf_wages": str(arrear_epf_wages),
            "arrear_ee_share": str(arrear_ee),
            "arrear_er_share": str(arrear_er),
            "arrear_eps_share": str(arrear_eps),
        }
        records.append(record)

        total_gross += gross
        total_epf_wages += epf_wages
        total_eps_wages += eps_wages
        total_edli_wages += edli_wages
        total_ee_share += ee_pf
        total_eps_share += eps_share
        total_er_share += er_share

    # Generate official EPFO #~# formatted lines
    raw_ecr_lines = []
    for r in records:
        line = (
            f"{r['uan']}#~#{r['member_name']}#~#{r['gross_wages']}#~#{r['epf_wages']}#~#"
            f"{r['eps_wages']}#~#{r['edli_wages']}#~#{r['ee_share']}#~#{r['eps_share']}#~#"
            f"{r['er_share']}#~#{r['ncp_days']}#~#{r['refund_of_advances']}#~#"
            f"{r['arrear_epf_wages']}#~#{r['arrear_ee_share']}#~#{r['arrear_er_share']}#~#{r['arrear_eps_share']}"
        )
        raw_ecr_lines.append(line)

    return {
        "report_type": "pf_ecr",
        "year": year,
        "month": month,
        "count": len(records),
        "totals": {
            "gross": str(total_gross),
            "epf_wages": str(total_epf_wages),
            "eps_wages": str(total_eps_wages),
            "edli_wages": str(total_edli_wages),
            "ee_share": str(total_ee_share),
            "eps_share": str(total_eps_share),
            "er_share": str(total_er_share),
            "total_remittance": str(total_ee_share + total_eps_share + total_er_share),
        },
        "records": records,
        "ecr_text": "\n".join(raw_ecr_lines),
    }


def generate_esi_return(tenant, year, month):
    """
    Generates ESIC Monthly Contribution Register & Return data.
    """
    payslips = Payslip.objects.filter(
        payroll_run__tenant=tenant,
        payroll_run__year=year,
        payroll_run__month=month,
    ).select_related('employee', 'payroll_run').order_by('employee__employee_code')

    records = []
    total_wages = Decimal('0.00')
    total_ip_contrib = Decimal('0.00')
    total_er_contrib = Decimal('0.00')

    for p in payslips:
        emp = p.employee
        # Include if employee had ESI deducted or gross is <= 21,000
        is_covered = (p.employee_esi > 0) or (emp.esi_covered and p.gross_salary <= Decimal('21000.00'))
        if not is_covered:
            continue

        ip_num = emp.esi_ip_number or ''
        ip_name = emp.full_name.strip()
        days_worked = p.present_days
        wages = p.gross_salary
        ip_contrib = p.employee_esi
        er_contrib = p.employer_esi
        total_contrib = ip_contrib + er_contrib

        reason = "0"
        if float(days_worked) == 0:
            reason = "Leave Without Pay (LOP)" if float(p.lop_days or 0) > 0 else "No Attendance"

        records.append({
            "ip_number": ip_num,
            "ip_name": ip_name,
            "employee_code": emp.employee_code,
            "no_of_days_worked": str(days_worked),
            "total_monthly_wages": str(wages),
            "ip_contribution": str(ip_contrib),
            "employer_contribution": str(er_contrib),
            "total_contribution": str(total_contrib),
            "reason_code": reason,
        })

        total_wages += wages
        total_ip_contrib += ip_contrib
        total_er_contrib += er_contrib

    return {
        "report_type": "esi_return",
        "year": year,
        "month": month,
        "count": len(records),
        "totals": {
            "total_wages": str(total_wages),
            "employee_contribution": str(total_ip_contrib),
            "employer_contribution": str(total_er_contrib),
            "total_esi_liability": str(total_ip_contrib + total_er_contrib),
        },
        "records": records,
    }


def generate_form_24q(tenant, financial_year_str, quarter):
    """
    Generates quarterly TDS return under Section 192 (Form 24Q).
    Quarters:
      Q1: April - June
      Q2: July - September
      Q3: October - December
      Q4: January - March
    """
    start_year, end_year = get_fy_date_range(financial_year_str)

    quarter_map = {
        'Q1': [(start_year, 4), (start_year, 5), (start_year, 6)],
        'Q2': [(start_year, 7), (start_year, 8), (start_year, 9)],
        'Q3': [(start_year, 10), (start_year, 11), (start_year, 12)],
        'Q4': [(end_year, 1), (end_year, 2), (end_year, 3)],
    }
    periods = quarter_map.get(quarter.upper(), quarter_map['Q1'])

    # Find payslips in this quarter
    from django.db.models import Q
    query = Q()
    for y, m in periods:
        query |= Q(payroll_run__year=y, payroll_run__month=m)

    payslips = Payslip.objects.filter(
        payroll_run__tenant=tenant
    ).filter(query).select_related('employee', 'payroll_run').order_by('employee__employee_code', 'payroll_run__year', 'payroll_run__month')

    records = []
    total_gross = Decimal('0.00')
    total_tds = Decimal('0.00')
    total_cess = Decimal('0.00')

    # Aggregate by employee or list line by line for Annexure I
    for p in payslips:
        emp = p.employee
        gross = p.gross_salary
        tds_val = p.tds

        # 4% Health & Education Cess is part of total TDS
        cess_val = (tds_val * Decimal('0.04') / Decimal('1.04')).quantize(Decimal('0.01')) if tds_val > 0 else Decimal('0.00')
        income_tax_val = tds_val - cess_val

        month_end = calendar.monthrange(p.payroll_run.year, p.payroll_run.month)[1]
        payment_date = f"{p.payroll_run.year:04d}-{p.payroll_run.month:02d}-{month_end:02d}"

        records.append({
            "employee_code": emp.employee_code,
            "employee_name": emp.full_name,
            "pan": emp.pan_number or "PANNOTAVBL",
            "section": "192",
            "month": p.payroll_run.month,
            "year": p.payroll_run.year,
            "payment_date": payment_date,
            "gross_amount": str(gross),
            "tds_deducted": str(tds_val),
            "income_tax": str(income_tax_val),
            "cess_4_pct": str(cess_val),
            "total_tax_deposited": str(tds_val),
            "challan_bsr": getattr(p.payroll_run, 'payment_account', None) and getattr(p.payroll_run.payment_account, 'account_number', '') or 'BSR-001',
            "challan_date": payment_date,
        })

        total_gross += gross
        total_tds += tds_val
        total_cess += cess_val

    return {
        "report_type": "tds_24q",
        "financial_year": f"{start_year}-{str(end_year)[-2:]}",
        "quarter": quarter.upper(),
        "count": len(records),
        "totals": {
            "gross_paid": str(total_gross),
            "tds_deducted": str(total_tds),
            "cess_component": str(total_cess),
            "total_tax_deposited": str(total_tds),
        },
        "records": records,
    }


def generate_form_16(tenant, financial_year_str, employee_id=None):
    """
    Generates Annual Form 16 Part B Summary for salary income and TDS under Section 203.
    Aggregates all 12 payroll runs across the Financial Year (1-Apr to 31-Mar).
    """
    start_year, end_year = get_fy_date_range(financial_year_str)

    # 12 months: (start_year, 4) to (end_year, 3)
    periods = [(start_year, m) for m in range(4, 13)] + [(end_year, m) for m in range(1, 4)]

    from django.db.models import Q
    query = Q()
    for y, m in periods:
        query |= Q(payroll_run__year=y, payroll_run__month=m)

    payslip_qs = Payslip.objects.filter(
        payroll_run__tenant=tenant
    ).filter(query).select_related('employee', 'payroll_run')

    if employee_id:
        payslip_qs = payslip_qs.filter(employee_id=employee_id)

    # Group payslips by employee
    emp_map = {}
    for p in payslip_qs:
        eid = str(p.employee.id)
        if eid not in emp_map:
            emp_map[eid] = {
                "employee": p.employee,
                "payslips": [],
            }
        emp_map[eid]["payslips"].append(p)

    records = []

    for eid, data in emp_map.items():
        emp = data["employee"]
        ps_list = data["payslips"]

        annual_gross = sum((p.gross_salary for p in ps_list), Decimal('0.00'))
        annual_pt = sum((p.professional_tax for p in ps_list), Decimal('0.00'))
        annual_tds = sum((p.tds for p in ps_list), Decimal('0.00'))

        regime = getattr(emp, 'tax_regime', 'new') or 'new'

        # Standard Deduction: ₹75,000 for New Regime (FY 2026-27), ₹50,000 for Old Regime
        if regime == 'new':
            std_deduction = min(Decimal('75000.00'), annual_gross)
        else:
            std_deduction = min(Decimal('50000.00'), annual_gross)

        # Net salary under section 17
        taxable_salary = max(Decimal('0.00'), annual_gross - std_deduction - annual_pt)

        # Chapter VI-A deductions (Old regime only: 80C, 80D, etc.)
        chapter_via = Decimal('0.00')
        if regime == 'old':
            from hr.models import EmployeeTaxDeclaration
            decl = EmployeeTaxDeclaration.objects.filter(employee=emp, financial_year__startswith=str(start_year)).first()
            if decl:
                sec_80c = min(decl.sec_80c_total or Decimal('0.00'), Decimal('150000.00'))
                sec_80d = min(decl.sec_80d_health_insurance or Decimal('0.00'), Decimal('75000.00'))
                chapter_via = sec_80c + sec_80d

        total_taxable_income = max(Decimal('0.00'), taxable_salary - chapter_via)

        # Tax liability calculation
        # New Regime FY 26-27 (Sec 115BAC):
        # 0 - 3L: 0%
        # 3L - 7L: 5% (max 20,000)
        # 7L - 10L: 10% (max 30,000)
        # 10L - 12L: 15% (max 30,000)
        # 12L - 15L: 20% (max 60,000)
        # > 15L: 30%
        tax_on_income = Decimal('0.00')
        if regime == 'new':
            rem = total_taxable_income
            if rem > Decimal('300000.00'):
                tier1 = min(rem - Decimal('300000.00'), Decimal('400000.00'))
                tax_on_income += tier1 * Decimal('0.05')
            if rem > Decimal('700000.00'):
                tier2 = min(rem - Decimal('700000.00'), Decimal('300000.00'))
                tax_on_income += tier2 * Decimal('0.10')
            if rem > Decimal('1000000.00'):
                tier3 = min(rem - Decimal('1000000.00'), Decimal('200000.00'))
                tax_on_income += tier3 * Decimal('0.15')
            if rem > Decimal('1200000.00'):
                tier4 = min(rem - Decimal('1200000.00'), Decimal('300000.00'))
                tax_on_income += tier4 * Decimal('0.20')
            if rem > Decimal('1500000.00'):
                tier5 = rem - Decimal('1500000.00')
                tax_on_income += tier5 * Decimal('0.30')

            # Sec 87A rebate for new regime: taxable income up to 7L has 0 tax
            if total_taxable_income <= Decimal('700000.00'):
                rebate_87a = tax_on_income
                tax_on_income = Decimal('0.00')
            else:
                rebate_87a = Decimal('0.00')
        else:
            # Old Regime slabs
            rem = total_taxable_income
            if rem > Decimal('250000.00'):
                tier1 = min(rem - Decimal('250000.00'), Decimal('250000.00'))
                tax_on_income += tier1 * Decimal('0.05')
            if rem > Decimal('500000.00'):
                tier2 = min(rem - Decimal('500000.00'), Decimal('500000.00'))
                tax_on_income += tier2 * Decimal('0.20')
            if rem > Decimal('1000000.00'):
                tier3 = rem - Decimal('1000000.00')
                tax_on_income += tier3 * Decimal('0.30')

            if total_taxable_income <= Decimal('500000.00'):
                rebate_87a = min(tax_on_income, Decimal('12500.00'))
                tax_on_income = max(Decimal('0.00'), tax_on_income - rebate_87a)
            else:
                rebate_87a = Decimal('0.00')

        cess = (tax_on_income * Decimal('0.04')).quantize(Decimal('0.01'))
        net_tax_liability = tax_on_income + cess
        tax_payable_or_refund = net_tax_liability - annual_tds

        records.append({
            "employee_id": str(emp.id),
            "employee_code": emp.employee_code,
            "employee_name": emp.full_name,
            "pan": emp.pan_number or "PANNOTAVBL",
            "tax_regime": regime.upper(),
            "gross_salary": str(annual_gross),
            "standard_deduction": str(std_deduction),
            "professional_tax": str(annual_pt),
            "income_chargeable_salaries": str(taxable_salary),
            "chapter_vi_a": str(chapter_via),
            "total_taxable_income": str(total_taxable_income),
            "tax_on_total_income": str(tax_on_income),
            "rebate_87a": str(rebate_87a),
            "cess_4_pct": str(cess),
            "net_tax_liability": str(net_tax_liability),
            "total_tds_deducted": str(annual_tds),
            "tax_payable_or_refund": str(tax_payable_or_refund),
            "payslips_count": len(ps_list),
        })

    return {
        "report_type": "form_16",
        "financial_year": f"{start_year}-{str(end_year)[-2:]}",
        "count": len(records),
        "records": records,
    }

"""UAT P1 #1: per-state PT with effective dates, driven by the employee's work-location state.

Odisha repeal row cites the Gazette (verified by Rudra CA); pre-repeal rows are 'VERIFY'."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.management import call_command
from django.test import TestCase

from hr.models import ProfessionalTaxSlab
from hr.services.payroll_engine import compute_pt, compute_pt_arrears

D = Decimal


class PTStateVersionedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs', verbosity=0)

    def pt(self, gross, state, y, m, employee=None, tenant=None):
        return compute_pt(D(gross), state, month=m, on_date=datetime.date(y, m, 28),
                          employee=employee, tenant=tenant)

    # Karnataka: gross >= 25k → ₹200, ₹300 in Feb; nil below.
    def test_karnataka_200_300_feb_and_nil_below(self):
        self.assertEqual(self.pt('25000', 'Karnataka', 2026, 10), D('200.00'))
        self.assertEqual(self.pt('25000', 'Karnataka', 2027, 2), D('300.00'))
        self.assertEqual(self.pt('24999.99', 'Karnataka', 2027, 2), D('0.00'))

    # Odisha: ₹0 from 1 Apr 2026 (repeal, per Rudra — to verify).
    def test_odisha_zero_from_apr_2026(self):
        for y, m in ((2026, 4), (2026, 10), (2027, 3)):
            self.assertEqual(self.pt('50000', 'Odisha', y, m), D('0.00'))

    # Arrears for periods before Apr 2026 keep the old Odisha slabs.
    def test_odisha_legacy_slabs_for_arrears(self):
        self.assertEqual(self.pt('13000', 'Odisha', 2026, 1), D('0.00'))
        self.assertEqual(self.pt('20000', 'Odisha', 2026, 1), D('125.00'))
        self.assertEqual(self.pt('30000', 'Odisha', 2026, 1), D('200.00'))
        self.assertEqual(self.pt('30000', 'Odisha', 2026, 3), D('300.00'))  # last month of FY
        self.assertEqual(self.pt('30000', 'Odisha', 2026, 2), D('200.00'))  # not Feb for Odisha

    def test_odisha_source_notes(self):
        rep = ProfessionalTaxSlab.objects.get(state_name='Odisha', effective_from=datetime.date(2026, 4, 1))
        self.assertEqual(rep.source_note,
                         'Odisha Gazette, Ordinance 02/2026, Extraordinary No. 1496 of 21-04-2026 (verified by Rudra CA)')
        legacy = ProfessionalTaxSlab.objects.filter(state_name='Odisha', effective_to=datetime.date(2026, 3, 31))
        self.assertEqual(legacy.count(), 3)
        for row in legacy:
            self.assertEqual(row.source_note, 'Pre-repeal Odisha schedule, VERIFY')
            self.assertNotIn('Rudra', row.source_note)

    def test_odisha_exact_monthly_thresholds_and_boundaries(self):
        bounds = sorted(ProfessionalTaxSlab.objects.filter(
            state_name='Odisha', effective_to__isnull=False).values_list('lower_bound', 'upper_bound'),
            key=lambda r: r[0])
        self.assertEqual(bounds, [(D('0.00'), D('13333.33')), (D('13333.33'), D('25000.00')),
                                  (D('25000.00'), None)])
        # <= 13,333.33 nil; > 13,333.33 and <= 25,000 Rs125; > 25,000 Rs200 (same edges as annual slabs)
        self.assertEqual(self.pt('13333.33', 'Odisha', 2026, 1), D('0.00'))
        self.assertEqual(self.pt('13333.34', 'Odisha', 2026, 1), D('125.00'))
        self.assertEqual(self.pt('25000.00', 'Odisha', 2026, 1), D('125.00'))
        self.assertEqual(self.pt('25000.01', 'Odisha', 2026, 1), D('200.00'))

    def test_arrears_slabbed_against_month_they_relate_to(self):
        emp = SimpleNamespace(work_state='Odisha', tenant=None,
                              pt_exempt_disability=False, pt_exempt_armed_forces=False)
        # Jan 2026 arrears paid in Oct 2026: Jan used the old slab (20k paid -> 125; revised 30k -> 200).
        self.assertEqual(compute_pt_arrears(emp, 1, 2026, D('20000'), D('30000')), D('75.00'))
        # The same arrears slabbed in the paid month (post-repeal) would wrongly be 0.
        self.assertEqual(compute_pt_arrears(emp, 10, 2026, D('20000'), D('30000')), D('0.00'))
        # Karnataka Feb arrears use the Feb amount of that month.
        emp.work_state = 'Karnataka'
        self.assertEqual(compute_pt_arrears(emp, 2, 2027, D('20000'), D('26000')), D('300.00'))

    # Driven by the employee's work-location state, not the company's state.
    def test_work_location_state_beats_company_state(self):
        company = SimpleNamespace(state='Odisha')
        emp = SimpleNamespace(work_state='Karnataka', tenant=company,
                              pt_exempt_disability=False, pt_exempt_armed_forces=False)
        self.assertEqual(self.pt('30000', '', 2026, 10, employee=emp, tenant=company), D('200.00'))
        emp.work_state = ''
        self.assertEqual(self.pt('30000', '', 2026, 10, employee=emp, tenant=company), D('0.00'))

    def test_state_code_accepted(self):
        self.assertEqual(self.pt('30000', '29', 2026, 10), D('200.00'))

    def test_reseed_is_idempotent_and_keeps_history(self):
        before = ProfessionalTaxSlab.objects.filter(state_name='Odisha').count()
        call_command('seed_pt_slabs', verbosity=0)
        self.assertEqual(ProfessionalTaxSlab.objects.filter(state_name='Odisha').count(), before)
        self.assertEqual(before, 4)

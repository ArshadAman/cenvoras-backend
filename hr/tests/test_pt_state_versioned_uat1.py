"""UAT P1 #1: per-state PT with effective dates, driven by the employee's work-location state.

Odisha data is 'per Rudra CA, to verify' (see hr/pt_slab_data.py)."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.management import call_command
from django.test import TestCase

from hr.models import ProfessionalTaxSlab
from hr.services.payroll_engine import compute_pt

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

    def test_odisha_rows_flagged_to_verify(self):
        notes = set(ProfessionalTaxSlab.objects.filter(state_name='Odisha').values_list('source_note', flat=True))
        self.assertTrue(notes and all('TO VERIFY' in n for n in notes))

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

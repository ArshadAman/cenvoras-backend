"""Professional Tax: state code / abbreviation must resolve to seeded slabs."""

from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from hr.services.payroll_engine import compute_pt


class ComputePTStateNormalizeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_pt_slabs')

    def test_karnataka_full_name_and_codes(self):
        for state in ('Karnataka', 'KA', '29', 'karnataka'):
            with self.subTest(state=state):
                self.assertEqual(
                    compute_pt(Decimal('50000.00'), state),
                    Decimal('200.00'),
                    f'PT should be ₹200 for state={state!r}',
                )

    def test_karnataka_below_threshold_nil(self):
        self.assertEqual(compute_pt(Decimal('20000.00'), 'KA'), Decimal('0.00'))

    def test_karnataka_february_taxable_band_is_300(self):
        """P0: Karnataka PT is ₹300 in February when gross is in the taxable band."""
        self.assertEqual(
            compute_pt(Decimal('50000.00'), 'Karnataka', month=2),
            Decimal('300.00'),
        )
        self.assertEqual(
            compute_pt(Decimal('30000.00'), 'KA', month=2),
            Decimal('300.00'),
        )
        # Below threshold stays nil even in February
        self.assertEqual(
            compute_pt(Decimal('20000.00'), 'Karnataka', month=2),
            Decimal('0.00'),
        )

    def test_empty_state_zero(self):
        self.assertEqual(compute_pt(Decimal('50000.00'), ''), Decimal('0.00'))
        self.assertEqual(compute_pt(Decimal('50000.00'), None), Decimal('0.00'))

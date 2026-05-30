"""
Unit tests for the seed_pt_slabs management command.

Requirements: 9.2, 12.2
- Test that running the command populates the database with 8 states' PT slabs.
- Ensure upper_bounds and pt_amounts are correct for a few sample cases.
"""

from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from hr.models import ProfessionalTaxSlab


class SeedPTSlabsCommandTests(TestCase):

    def test_seed_pt_slabs_command(self):
        """Test that the seed_pt_slabs command populates the PT slabs for 8 states."""
        
        # Initially, there should be no slabs
        self.assertEqual(ProfessionalTaxSlab.objects.count(), 0)
        
        # Run the command
        call_command('seed_pt_slabs')
        
        # Check that slabs were created
        self.assertGreater(ProfessionalTaxSlab.objects.count(), 0)
        
        # Check for the 8 specific states
        states = ProfessionalTaxSlab.objects.values_list('state_name', flat=True).distinct()
        expected_states = {
            "Maharashtra", "Karnataka", "West Bengal", "Tamil Nadu", 
            "Andhra Pradesh", "Telangana", "Gujarat", "Madhya Pradesh"
        }
        self.assertEqual(set(states), expected_states)
        self.assertEqual(len(set(states)), 8)
        
        # Check specific edge cases
        # Maharashtra highest slab
        mh_top = ProfessionalTaxSlab.objects.get(state_name='Maharashtra', lower_bound=10001)
        self.assertIsNone(mh_top.upper_bound)
        self.assertEqual(mh_top.pt_amount, Decimal('200.00'))
        
        # Karnataka lowest slab
        ka_bottom = ProfessionalTaxSlab.objects.get(state_name='Karnataka', lower_bound=0)
        self.assertEqual(ka_bottom.upper_bound, 14999)
        self.assertEqual(ka_bottom.pt_amount, Decimal('0.00'))
        
        # Re-running the command should be idempotent
        initial_count = ProfessionalTaxSlab.objects.count()
        call_command('seed_pt_slabs')
        self.assertEqual(ProfessionalTaxSlab.objects.count(), initial_count)

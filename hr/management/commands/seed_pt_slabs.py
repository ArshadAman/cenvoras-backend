from django.core.management.base import BaseCommand
from hr.models import ProfessionalTaxSlab

class Command(BaseCommand):
    help = 'Seeds default Professional Tax slabs for 8 Indian states'

    def handle(self, *args, **options):
        # State, lower_bound, upper_bound, pt_amount
        slabs = [
            ("Maharashtra", 0, 7500, 0),
            ("Maharashtra", 7501, 10000, 175),
            ("Maharashtra", 10001, None, 200),
            
            ("Karnataka", 0, 14999, 0),
            ("Karnataka", 15000, None, 200),
            
            ("West Bengal", 0, 10000, 0),
            ("West Bengal", 10001, 15000, 110),
            ("West Bengal", 15001, 25000, 130),
            ("West Bengal", 25001, 40000, 150),
            ("West Bengal", 40001, None, 200),
            
            ("Tamil Nadu", 0, 3500, 0),
            ("Tamil Nadu", 3501, 5000, 22.50),
            ("Tamil Nadu", 5001, 7500, 52.50),
            ("Tamil Nadu", 7501, 10000, 115),
            ("Tamil Nadu", 10001, 12500, 171),
            ("Tamil Nadu", 12501, None, 208),
            
            ("Andhra Pradesh", 0, 15000, 0),
            ("Andhra Pradesh", 15001, 20000, 150),
            ("Andhra Pradesh", 20001, None, 200),
            
            ("Telangana", 0, 15000, 0),
            ("Telangana", 15001, 20000, 150),
            ("Telangana", 20001, None, 200),
            
            ("Gujarat", 0, 11999, 0),
            ("Gujarat", 12000, None, 200),
            
            ("Madhya Pradesh", 0, 22500, 0),
            ("Madhya Pradesh", 22501, 30000, 125),
            ("Madhya Pradesh", 30001, 40000, 167),
            ("Madhya Pradesh", 40001, None, 200),
        ]

        count = 0
        for state, lower, upper, amount in slabs:
            obj, created = ProfessionalTaxSlab.objects.update_or_create(
                state_name=state,
                lower_bound=lower,
                defaults={
                    'upper_bound': upper,
                    'pt_amount': amount
                }
            )
            count += 1
            
        self.stdout.write(self.style.SUCCESS(f'Successfully seeded {count} Professional Tax slabs for 8 states.'))

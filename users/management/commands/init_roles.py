from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from billing.models import SalesInvoice, PurchaseBill, Payment
from inventory.models import Product, StockPoint, StockTransfer

class Command(BaseCommand):
    help = 'Initialize default roles and permissions'

    def handle(self, *args, **options):
        # Define Roles
        roles = {
            'Admin': 'all',
            'Manager': ['view_product', 'add_product', 'change_product', 
                        'view_salesinvoice', 'add_salesinvoice', 'change_salesinvoice',
                        'view_purchasebill', 'add_purchasebill', 'change_purchasebill',
                        'view_payment', 'add_payment',
                        'view_stocktransfer', 'add_stocktransfer',
                        'view_stockpoint'],
            'Cashier': ['view_product', 
                        'view_salesinvoice', 'add_salesinvoice',
                        'view_payment', 'add_payment']
        }

        for role, perms in roles.items():
            group, created = Group.objects.get_or_create(name=role)
            if created:
                self.stdout.write(f'Created group {role}')
            else:
                self.stdout.write(f'Updating group {role}')

            if perms == 'all':
                # Grant all permissions
                # For safety, maybe just giving superuser status is better for admins, 
                # but let's give all app permissions
                all_perms = Permission.objects.all()
                group.permissions.set(all_perms)
            else:
                # Grant specific permissions
                perm_objects = []
                for codename in perms:
                    try:
                        perm = Permission.objects.get(codename=codename)
                        perm_objects.append(perm)
                    except Permission.DoesNotExist:
                        self.stdout.write(self.style.WARNING(f'Permission {codename} not found'))
                
                group.permissions.set(perm_objects)

        self.stdout.write(self.style.SUCCESS('Successfully initialized roles'))

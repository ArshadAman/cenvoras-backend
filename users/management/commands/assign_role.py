from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Assign a role to a user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Username/Email of the user')
        parser.add_argument('role', type=str, help='Role: admin, manager, salesman, accountant')

    def handle(self, *args, **options):
        username = options['username']
        role = options['role']
        
        try:
            user = User.objects.get(username=username)
            if role not in dict(User.ROLE_CHOICES):
                self.stdout.write(self.style.ERROR(f'Invalid role. Choices: {dict(User.ROLE_CHOICES).keys()}'))
                return
            
            user.role = role
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Successfully assigned role "{role}" to user "{username}"'))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User "{username}" not found'))

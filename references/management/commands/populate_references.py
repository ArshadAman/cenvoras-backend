from django.core.management.base import BaseCommand
from django.db import transaction
from references.models import HSNCode, GSTRate
from references.services import get_provider
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Populate HSN codes and GST rates from external provider'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data before populating',
        )
        parser.add_argument(
            '--provider',
            type=str,
            default='mock',
            help='Data provider to use (mock, google)',
        )
    
    @transaction.atomic
    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            HSNCode.objects.all().delete()
            GSTRate.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Data cleared.'))
        
        provider = get_provider()
        
        # Populate HSN codes
        self.stdout.write('Fetching HSN codes...')
        hsn_data = provider.fetch_hsn_codes()
        
        hsn_count = 0
        for item in hsn_data:
            hsn, created = HSNCode.objects.update_or_create(
                code=item['code'],
                defaults={
                    'description': item['description'],
                    'category': item.get('category', ''),
                }
            )
            if created:
                hsn_count += 1
        
        self.stdout.write(self.style.SUCCESS(f'Processed {len(hsn_data)} HSN codes ({hsn_count} new)'))
        
        # Populate GST rates
        self.stdout.write('Fetching GST rates...')
        gst_data = provider.fetch_gst_rates()
        
        gst_count = 0
        for item in gst_data:
            gst, created = GSTRate.objects.update_or_create(
                category=item['category'],
                rate=item['rate'],
                defaults={
                    'hsn_codes': item.get('hsn_codes', ''),
                    'notes': item.get('notes', ''),
                }
            )
            if created:
                gst_count += 1
        
        self.stdout.write(self.style.SUCCESS(f'Processed {len(gst_data)} GST rates ({gst_count} new)'))
        
        # Summary
        total_hsn = HSNCode.objects.count()
        total_gst = GSTRate.objects.count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n✓ Successfully populated database:\n'
                f'  - {total_hsn} HSN codes\n'
                f'  - {total_gst} GST rates\n'
            )
        )

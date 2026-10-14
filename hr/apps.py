import logging
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class HrConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'hr'
    verbose_name = 'Human Resources'

    def ready(self):
        # PT slabs are not created by migrations — seed once if the table is empty.
        if any(cmd in sys.argv for cmd in ('migrate', 'makemigrations', 'test', 'collectstatic')):
            return
        try:
            from hr.models import ProfessionalTaxSlab
            if not ProfessionalTaxSlab.objects.exists():
                from django.core.management import call_command
                call_command('seed_pt_slabs', verbosity=0)
                logger.info('Seeded Professional Tax slabs (table was empty).')
        except Exception:
            # DB may not be ready during first migrate / cold start
            pass

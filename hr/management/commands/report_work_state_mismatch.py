"""READ-ONLY: list employees whose PT work state differs from their company's state.

Writes nothing. Some employees may genuinely work in another state, so review by hand and
correct individual profiles in the UI (Edit Profile > Compensation & Tax > Work State).
"""
from django.core.management.base import BaseCommand

from hr.models import Employee
from hr.services.work_state import canonical_work_state, resolve_work_state


class Command(BaseCommand):
    help = "List (read-only) employees whose work_state differs from the company state."

    def add_arguments(self, parser):
        parser.add_argument('--tenant', help='Limit to one tenant (user id).')
        parser.add_argument('--include-inactive', action='store_true')

    def handle(self, *args, **opts):
        qs = Employee.objects.select_related('tenant').order_by('tenant_id', 'employee_code')
        if opts.get('tenant'):
            qs = qs.filter(tenant_id=opts['tenant'])
        if not opts.get('include_inactive'):
            qs = qs.filter(status__in=['active', 'probation', 'notice_period'])
        n = bad = 0
        self.stdout.write('tenant_id\temployee_code\tname\tstored_work_state\tcompany_state\tissue')
        for emp in qs.iterator():
            company = resolve_work_state(tenant=emp.tenant, work_state='')
            stored_raw = (emp.work_state or '').strip()
            if stored_raw and canonical_work_state(stored_raw) is None:
                bad += 1
                self.stdout.write(f"{emp.tenant_id}\t{emp.employee_code}\t{emp.full_name}\t"
                                  f"{stored_raw}\t{company or '(unknown)'}\tINVALID state value")
                continue
            stored = (canonical_work_state(stored_raw) or '') if stored_raw else ''
            if not company or stored.lower() == company.lower():
                continue
            n += 1
            self.stdout.write(f"{emp.tenant_id}\t{emp.employee_code}\t{emp.full_name}\t"
                              f"{stored_raw or '(blank)'}\t{company}\tdiffers from company")
        self.stdout.write(f'{n} employee(s) differ from their company state; {bad} have an invalid '
                          f'stored work state. Nothing was changed.')

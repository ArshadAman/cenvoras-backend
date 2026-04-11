from django.db import migrations


def forwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')

    for plan in Plan.objects.all():
        if plan.code == 'starter':
            plan.code = 'free'
            plan.name = 'Free'
            plan.max_managers = 0
            plan.max_invoices_per_month = 50
        elif plan.code == 'growth':
            plan.code = 'pro'
            plan.name = 'Pro'
            plan.max_managers = 2
            plan.max_invoices_per_month = -1
        elif plan.code == 'enterprise':
            plan.code = 'business'
            plan.name = 'Business'
            plan.max_managers = 5
            plan.max_invoices_per_month = -1

        plan.max_customers = -1
        plan.max_team_members = plan.max_managers
        plan.save(update_fields=['code', 'name', 'max_managers', 'max_customers', 'max_team_members', 'max_invoices_per_month'])


def backwards(apps, schema_editor):
    Plan = apps.get_model('subscription', 'Plan')

    for plan in Plan.objects.all():
        if plan.code == 'free':
            plan.code = 'starter'
            plan.name = 'Starter Plan'
            plan.max_managers = 0
            plan.max_invoices_per_month = 50
        elif plan.code == 'pro':
            plan.code = 'growth'
            plan.name = 'Growth Plan'
            plan.max_managers = 2
            plan.max_invoices_per_month = -1
        elif plan.code == 'business':
            plan.code = 'enterprise'
            plan.name = 'Business Plan'
            plan.max_managers = 5
            plan.max_invoices_per_month = -1

        plan.save(update_fields=['code', 'name', 'max_managers', 'max_invoices_per_month'])


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0003_add_plan_limits'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from users.models import User
from hr.models import PayrollRun
from audit_log.models import AuditLog

class PayrollRunAPITests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='password123',
            role='admin'
        )
        self.client.force_authenticate(user=self.tenant)
        
        self.draft_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=10,
            year=2023,
            status='draft'
        )
        
        self.finalised_run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=11,
            year=2023,
            status='finalised'
        )

    def test_run_finalised_payroll_returns_400(self):
        url = reverse('payroll-run-run', kwargs={'pk': self.finalised_run.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('payroll_already_finalised', str(response.data))
        
    def test_run_draft_payroll(self):
        url = reverse('payroll-run-run', kwargs={'pk': self.draft_run.pk})
        response = self.client.post(url)
        # Should return 200 or 202 because it triggers celery task
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_202_ACCEPTED])
        
        self.draft_run.refresh_from_db()
        self.assertEqual(self.draft_run.status, 'processing')

    def test_finalise_payroll(self):
        # Must be in completed state to finalise
        run = PayrollRun.objects.create(
            tenant=self.tenant,
            month=12,
            year=2023,
            status='completed'
        )
        url = reverse('payroll-run-finalise', kwargs={'pk': run.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        run.refresh_from_db()
        self.assertEqual(run.status, 'finalised')
        self.assertIsNotNone(run.finalised_at)
        
        # Check audit log
        audit = AuditLog.objects.filter(
            action='UPDATE',
            model_name='PayrollRun',
            object_id=str(run.id)
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.changes['after']['status'], 'finalised')

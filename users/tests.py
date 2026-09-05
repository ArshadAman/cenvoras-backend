from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

class PasswordChangeTests(APITestCase):
    def setUp(self):
        # Create a tenant (admin) user
        self.tenant = User.objects.create_user(
            username='tenant@example.com',
            email='tenant@example.com',
            password='old_password123',
            role='admin'
        )
        
        # Create an employee user
        self.employee = User.objects.create_user(
            username='employee@example.com',
            email='employee@example.com',
            password='employee_old_password',
            role='employee',
            parent=self.tenant
        )
        
        self.url = reverse('change_password')

    def test_tenant_change_password_success(self):
        self.client.force_authenticate(user=self.tenant)
        data = {
            'current_password': 'old_password123',
            'new_password': 'new_password123',
            'confirm_new_password': 'new_password123'
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Password updated successfully.')
        
        # Verify password actually changed
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.check_password('new_password123'))

    def test_employee_change_password_success(self):
        self.client.force_authenticate(user=self.employee)
        data = {
            'current_password': 'employee_old_password',
            'new_password': 'employee_new_password',
            'confirm_new_password': 'employee_new_password'
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Password updated successfully.')
        
        # Verify password actually changed
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password('employee_new_password'))

    def test_change_password_missing_fields(self):
        self.client.force_authenticate(user=self.tenant)
        data = {
            'current_password': 'old_password123',
            # missing new_password
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_change_password_mismatch(self):
        self.client.force_authenticate(user=self.tenant)
        data = {
            'current_password': 'old_password123',
            'new_password': 'new_password123',
            'confirm_new_password': 'different_password'
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('confirm_new_password', response.data)

    def test_change_password_too_short(self):
        self.client.force_authenticate(user=self.tenant)
        data = {
            'current_password': 'old_password123',
            'new_password': 'short',
            'confirm_new_password': 'short'
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('new_password', response.data)

    def test_change_password_incorrect_current(self):
        self.client.force_authenticate(user=self.tenant)
        data = {
            'current_password': 'wrong_password',
            'new_password': 'new_password123',
            'confirm_new_password': 'new_password123'
        }
        response = self.client.patch(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('current_password', response.data)

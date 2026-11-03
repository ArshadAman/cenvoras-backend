"""UAT P1 item 13 (validation part): company setup rejects a blank legal name or address."""
from rest_framework.test import APITestCase

from users.models import User


class CompanySetupRequiredTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='setup1', email='setup1@example.com', password='Secret123!',
                                             business_name='UAT Sharma Traders', business_address='Cuttack, Odisha')
        self.client.force_authenticate(self.user)

    def test_update_blank_business_name_rejected(self):
        res = self.client.patch('/api/users/profile/update/', {'business_name': '   '}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('business_name', str(res.data))
        self.user.refresh_from_db()
        self.assertEqual(self.user.business_name, 'UAT Sharma Traders')

    def test_update_blank_address_rejected(self):
        res = self.client.patch('/api/users/profile/update/', {'business_address': ''}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('business_address', str(res.data))

    def test_update_other_fields_ok_and_trimmed(self):
        res = self.client.patch('/api/users/profile/update/', {'business_name': '  UAT Sharma Traders Pvt  ',
                                                                'city': 'Cuttack'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.business_name, 'UAT Sharma Traders Pvt')

    def test_setup_requires_name_and_address(self):
        u = User.objects.create_user(username='setup2', email='setup2@example.com', password='Secret123!',
                                     business_name='X')
        self.client.force_authenticate(u)
        res = self.client.put('/api/users/profile/setup/', {'business_name': 'Y', 'business_address': ' '},
                              format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn('business_address', str(res.data))

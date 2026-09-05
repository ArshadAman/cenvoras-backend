"""
Unit tests for Salary Structure and Assignment APIs.

Requirements: 7.3, 7.5, 7.6
- Test creating a structure without a Basic component returns HTTP 400
- Test computed_components snapshot is stored correctly on assignment
- Test historical assignments are preserved when a new assignment is created
"""

import datetime
from decimal import Decimal
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from hr.models import Department, Designation, Employee, SalaryStructure, EmployeeSalaryAssignment


class SalaryAPITests(APITestCase):

    def setUp(self):
        self.tenant = User.objects.create_user(
            username='tenant_owner',
            email='tenant@example.com',
            password='testpass',
            business_name='Tenant Corp',
            role='admin'
        )
        self.dept = Department.objects.create(tenant=self.tenant, name='Engineering')
        self.desig = Designation.objects.create(tenant=self.tenant, name='Developer')
        self.emp = Employee.objects.create(
            tenant=self.tenant,
            full_name='Alice',
            date_of_birth=datetime.date(1990, 1, 1),
            date_of_joining=datetime.date(2023, 1, 1),
            gender='F',
            employment_type='full_time',
            department=self.dept,
            designation=self.desig,
            work_state='Maharashtra',
            status='active'
        )
        self.structure_url = reverse('salary-structure-list')
        self.assignment_url = reverse('salary-assignment-list')
        self.client.force_authenticate(user=self.tenant)

    def test_create_structure_without_basic_returns_400(self):
        """Test that creating a structure without a Basic component returns HTTP 400."""
        payload = {
            'name': 'Invalid Structure',
            'components': [
                {'name': 'HRA', 'component_type': 'pct_basic', 'value': '40.00', 'is_basic': False}
            ]
        }
        res = self.client.post(self.structure_url, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Exactly one component must be designated as the Basic component", str(res.data))

    def test_computed_components_snapshot(self):
        """Test that computed_components snapshot is stored correctly on assignment."""
        # Create a valid structure
        structure_payload = {
            'name': 'Standard Structure',
            'components': [
                {'name': 'Basic', 'component_type': 'pct_gross', 'value': '50.00', 'is_basic': True},
                {'name': 'HRA', 'component_type': 'pct_basic', 'value': '40.00', 'is_basic': False},
                {'name': 'Special Allowance', 'component_type': 'fixed', 'value': '2000.00', 'is_basic': False}
            ]
        }
        res_struct = self.client.post(self.structure_url, structure_payload, format='json')
        self.assertEqual(res_struct.status_code, status.HTTP_201_CREATED)
        structure_id = res_struct.data['id']

        # Create assignment
        assignment_payload = {
            'employee': self.emp.id,
            'salary_structure': structure_id,
            'monthly_ctc': '50000.00',
            'effective_from': '2023-01-01'
        }
        res_assign = self.client.post(self.assignment_url, assignment_payload, format='json')
        self.assertEqual(res_assign.status_code, status.HTTP_201_CREATED)

        computed = res_assign.data['computed_components']
        
        # CTC = 50000
        # Basic = 50% of 50000 = 25000
        # HRA = 40% of Basic = 10000
        # Special = 2000
        self.assertEqual(Decimal(computed['Basic']), Decimal('25000.00'))
        self.assertEqual(Decimal(computed['HRA']), Decimal('10000.00'))
        self.assertEqual(Decimal(computed['Special Allowance']), Decimal('2000.00'))

    def test_historical_assignments_preserved(self):
        """Test that historical assignments are preserved when a new assignment is created."""
        structure = SalaryStructure.objects.create(tenant=self.tenant, name='Simple')
        structure.components.create(name='Basic', component_type='pct_gross', value=Decimal('100.00'), is_basic=True)

        # First assignment
        EmployeeSalaryAssignment.objects.create(
            tenant=self.tenant,
            employee=self.emp,
            salary_structure=structure,
            monthly_ctc=Decimal('40000.00'),
            effective_from=datetime.date(2023, 1, 1),
            computed_components={'Basic': '40000.00'}
        )

        # Create a new assignment via API
        payload = {
            'employee': self.emp.id,
            'salary_structure': structure.id,
            'monthly_ctc': '60000.00',
            'effective_from': '2024-01-01'
        }
        res = self.client.post(self.assignment_url, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Verify both assignments exist
        assignments = EmployeeSalaryAssignment.objects.filter(employee=self.emp).order_by('-effective_from')
        self.assertEqual(assignments.count(), 2)
        
        # Newest should be the 60000 one
        self.assertEqual(assignments[0].monthly_ctc, Decimal('60000.00'))
        self.assertEqual(assignments[0].computed_components['Basic'], '60000.00')
        
        # Oldest should be the 40000 one
        self.assertEqual(assignments[1].monthly_ctc, Decimal('40000.00'))
        self.assertEqual(assignments[1].computed_components['Basic'], '40000.00')

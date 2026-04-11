from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Plan
from .services import get_entitlements


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def subscription_entitlements(request):
	return Response({
		'success': True,
		'data': get_entitlements(request.user),
	})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def plan_catalog(request):
	plans = Plan.objects.filter(is_active=True).prefetch_related('features').order_by('monthly_price', 'name')
	data = []
	for plan in plans:
		data.append({
			'id': str(plan.id),
			'code': plan.code,
			'name': plan.name,
			'description': plan.description,
			'monthly_price': str(plan.monthly_price),
			'yearly_price': str(plan.yearly_price),
			'max_managers': plan.max_managers,
			'max_team_members': getattr(plan, 'max_team_members', plan.max_managers),
			'max_customers': getattr(plan, 'max_customers', -1),
			'max_invoices_per_month': plan.max_invoices_per_month,
			'features': [feature.code for feature in plan.features.all()],
		})

	return Response({
		'success': True,
		'data': data,
	})

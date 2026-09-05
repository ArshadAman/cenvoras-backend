from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import condition
from django.http import JsonResponse
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
import json
from datetime import datetime

from .models import HSNCode, GSTRate
from .services import get_provider


def get_hsn_etag(request, slug):
    """ETag generator for HSN code pages (for HTTP caching)."""
    try:
        hsn = HSNCode.objects.get(slug=slug)
        return str(hsn.updated_at)
    except HSNCode.DoesNotExist:
        return None


def get_gst_etag(request, slug):
    """ETag generator for GST rate pages."""
    try:
        gst = GSTRate.objects.get(slug=slug)
        return str(gst.updated_at)
    except GSTRate.DoesNotExist:
        return None


def hsn_code_detail(request, slug):
    """
    Renders a single HSN code page with SEO meta tags and JSON-LD structured data.
    Example: /hsn/8471-automatic-data-processing-machines/
    """
    hsn = get_object_or_404(HSNCode, slug=slug)
    
    # Generate JSON-LD structured data (Schema.org)
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": hsn.meta_title,
        "description": hsn.meta_description,
        "author": {
            "@type": "Organization",
            "name": "Cenvora",
            "url": request.build_absolute_uri('/'),
        },
        "datePublished": hsn.created_at.isoformat(),
        "dateModified": hsn.updated_at.isoformat(),
        "mainEntity": {
            "@type": "Thing",
            "name": f"HSN Code {hsn.code}",
            "description": hsn.description,
            "identifier": hsn.code,
            "category": hsn.category or "Product Classification",
        },
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "5",
            "bestRating": "5",
            "ratingCount": "1",
            "description": "Accurate HSN code information for GST compliance"
        }
    }
    
    # Related GST rates
    related_gst_rates = GSTRate.objects.filter(
        hsn_codes__contains=hsn.code
    ).values('rate', 'category', 'slug').distinct()
    
    context = {
        'hsn': hsn,
        'json_ld': json.dumps(json_ld),
        'related_gst_rates': related_gst_rates,
        'page_title': hsn.meta_title,
        'page_description': hsn.meta_description,
        'canonical_url': request.build_absolute_uri(f'/hsn/{hsn.slug}/'),
    }
    
    return render(request, 'references/hsn_detail.html', context)


def gst_rate_detail(request, slug):
    """
    Renders a single GST rate page with SEO meta tags and JSON-LD.
    Example: /gst-rate/gst-18-electronics/
    """
    gst = get_object_or_404(GSTRate, slug=slug)
    
    # Generate JSON-LD structured data
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": gst.meta_title,
        "description": gst.meta_description,
        "author": {
            "@type": "Organization",
            "name": "Cenvora",
            "url": request.build_absolute_uri('/'),
        },
        "datePublished": gst.created_at.isoformat(),
        "dateModified": gst.updated_at.isoformat(),
        "mainEntity": {
            "@type": "Thing",
            "name": f"GST Rate {gst.rate}%",
            "description": f"GST rate for {gst.category}",
            "taxRate": f"{gst.rate}%",
            "applicableTo": gst.category,
            "effectiveDate": gst.effective_from.isoformat() if gst.effective_from else datetime.now().isoformat(),
        }
    }
    
    # Related HSN codes
    hsn_codes_list = gst.get_hsn_codes_list()
    related_hsns = HSNCode.objects.filter(code__in=hsn_codes_list)
    
    context = {
        'gst': gst,
        'json_ld': json.dumps(json_ld),
        'related_hsns': related_hsns,
        'page_title': gst.meta_title,
        'page_description': gst.meta_description,
        'canonical_url': request.build_absolute_uri(f'/gst-rate/{gst.slug}/'),
    }
    
    return render(request, 'references/gst_detail.html', context)


@api_view(['GET'])
@permission_classes([AllowAny])
def hsn_search(request):
    """
    API endpoint to search HSN codes.
    Query params: q (search term), limit (default 10)
    """
    q = request.query_params.get('q', '').strip()
    limit = min(int(request.query_params.get('limit', 10)), 50)
    
    if len(q) < 2:
        return Response({'success': False, 'message': 'Query too short', 'data': []}, status=400)
    
    hsns = HSNCode.objects.filter(
        Q(code__icontains=q) | Q(description__icontains=q) | Q(category__icontains=q)
    )[:limit]
    
    data = [
        {'code': h.code, 'description': h.description, 'category': h.category, 'slug': h.slug}
        for h in hsns
    ]
    
    return Response({'success': True, 'data': data})


@api_view(['GET'])
@permission_classes([AllowAny])
def gst_rate_by_category(request):
    """
    API endpoint to get GST rate for a category.
    Query params: category (required)
    """
    category = request.query_params.get('category', '').strip()
    
    if not category:
        return Response({'success': False, 'message': 'Category required'}, status=400)
    
    gst_rates = GSTRate.objects.filter(category__icontains=category)
    
    data = [
        {
            'rate': g.rate,
            'category': g.category,
            'hsn_codes': g.get_hsn_codes_list(),
            'slug': g.slug,
            'notes': g.notes,
        }
        for g in gst_rates
    ]
    
    return Response({'success': True, 'data': data})


@api_view(['GET'])
@permission_classes([AllowAny])
def hsn_gst_combined(request, hsn_code):
    """
    Combined endpoint: Given an HSN code, return the HSN details + applicable GST rate.
    Example: /api/references/hsn-gst/8471/
    """
    try:
        hsn = HSNCode.objects.get(code=hsn_code)
    except HSNCode.DoesNotExist:
        return Response({'success': False, 'message': 'HSN code not found'}, status=404)
    
    gst_rates = GSTRate.objects.filter(hsn_codes__contains=hsn_code)
    
    data = {
        'hsn': {
            'code': hsn.code,
            'description': hsn.description,
            'category': hsn.category,
            'slug': hsn.slug,
        },
        'gst_rates': [
            {
                'rate': g.rate,
                'category': g.category,
                'slug': g.slug,
                'notes': g.notes,
            }
            for g in gst_rates
        ]
    }
    
    return Response({'success': True, 'data': data})


@api_view(['GET'])
@permission_classes([AllowAny])
def reference_stats(request):
    """
    Public endpoint showing stats on HSN codes and GST rates (good for crawlers).
    """
    stats = {
        'total_hsn_codes': HSNCode.objects.count(),
        'total_gst_rates': GSTRate.objects.count(),
        'categories': list(HSNCode.objects.values_list('category', flat=True).distinct()),
        'gst_rate_options': [rate[0] for rate in GSTRate.GST_RATE_CHOICES],
    }
    
    return Response({'success': True, 'data': stats})

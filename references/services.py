import requests
from typing import List, Dict, Any
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class HSNGSTProvider:
    """Base class for HSN and GST data providers."""
    
    def fetch_hsn_codes(self) -> List[Dict[str, Any]]:
        """Fetch list of HSN codes. Returns list of dicts with keys: code, description, category."""
        raise NotImplementedError
    
    def fetch_gst_rates(self) -> List[Dict[str, Any]]:
        """Fetch GST rates. Returns list of dicts with keys: category, rate, hsn_codes, notes."""
        raise NotImplementedError


class MockHSNGSTProvider(HSNGSTProvider):
    """Mock provider for development/testing. Contains sample Indian product data."""
    
    def fetch_hsn_codes(self) -> List[Dict[str, Any]]:
        """Sample HSN codes based on actual Indian HSN classifications."""
        return [
            # Electronics & IT
            {"code": "8471", "description": "Automatic Data Processing Machines", "category": "Electronics"},
            {"code": "8517", "description": "Telephone Sets and Related Equipment", "category": "Electronics"},
            {"code": "8528", "description": "Television Sets and Reception Apparatus", "category": "Electronics"},
            {"code": "8504", "description": "Electrical Transformers and Converters", "category": "Electronics"},
            {"code": "8503", "description": "Electric Motors and Generators", "category": "Electronics"},
            
            # Textiles
            {"code": "6204", "description": "Women's Clothing", "category": "Textiles"},
            {"code": "6203", "description": "Men's Clothing", "category": "Textiles"},
            {"code": "5903", "description": "Textile Fabrics", "category": "Textiles"},
            {"code": "6005", "description": "Knitted Fabrics", "category": "Textiles"},
            
            # Pharmaceuticals
            {"code": "3004", "description": "Medicaments (Pharmaceutical Products)", "category": "Pharmaceuticals"},
            {"code": "3002", "description": "Medicinal Extracts", "category": "Pharmaceuticals"},
            {"code": "3001", "description": "Glands and Organs", "category": "Pharmaceuticals"},
            
            # Machinery
            {"code": "8479", "description": "Industrial Machinery and Parts", "category": "Machinery"},
            {"code": "8475", "description": "Machinery for Food and Agriculture", "category": "Machinery"},
            {"code": "8704", "description": "Motor Vehicles (Commercial)", "category": "Machinery"},
            
            # Chemicals
            {"code": "2905", "description": "Acyclic Alcohols", "category": "Chemicals"},
            {"code": "2917", "description": "Polycarboxylic Acids", "category": "Chemicals"},
            {"code": "3208", "description": "Paints and Coatings", "category": "Chemicals"},
            
            # Jewellery
            {"code": "7113", "description": "Jewellery and Parts", "category": "Jewellery"},
            {"code": "7107", "description": "Unwrought Precious Metals", "category": "Jewellery"},
            
            # Food & Beverages
            {"code": "2201", "description": "Water", "category": "Food & Beverages"},
            {"code": "2202", "description": "Non-alcoholic Beverages", "category": "Food & Beverages"},
            {"code": "0901", "description": "Coffee, Cocoa, Tea", "category": "Food & Beverages"},
            {"code": "1905", "description": "Bakery Products", "category": "Food & Beverages"},
        ]
    
    def fetch_gst_rates(self) -> List[Dict[str, Any]]:
        """Sample GST rates for Indian products (2026)."""
        return [
            {
                "category": "Electronics and Accessories",
                "rate": 18,
                "hsn_codes": "8471,8517,8528,8504",
                "notes": "Includes mobile phones, laptops, televisions. Some items like mobile chargers may have different rates.",
            },
            {
                "category": "Textiles and Clothing",
                "rate": 5,
                "hsn_codes": "6204,6203,5903,6005",
                "notes": "Clothes and textiles. Branded/imported items may have higher GST.",
            },
            {
                "category": "Pharmaceuticals",
                "rate": 5,
                "hsn_codes": "3004,3002,3001",
                "notes": "Most medicines are 5% GST. OTC drugs and formulations vary.",
            },
            {
                "category": "Industrial Machinery",
                "rate": 18,
                "hsn_codes": "8479,8475",
                "notes": "Capital goods. Eligible for GST Input Credit.",
            },
            {
                "category": "Chemicals and Paints",
                "rate": 18,
                "hsn_codes": "2905,2917,3208",
                "notes": "Industrial and commercial chemicals.",
            },
            {
                "category": "Precious Metals and Jewellery",
                "rate": 5,
                "hsn_codes": "7113,7107",
                "notes": "Gold and precious metals at concessional rate. Hallmark certification required.",
            },
            {
                "category": "Food and Beverages",
                "rate": 5,
                "hsn_codes": "2201,2202,0901,1905",
                "notes": "Most food items are 5% GST. Alcohol has different treatment.",
            },
            {
                "category": "Motor Vehicles",
                "rate": 28,
                "hsn_codes": "8704,8703",
                "notes": "Highest GST rate. Applicable to cars, commercial vehicles.",
            },
        ]


class GoogleDataProvider(HSNGSTProvider):
    """Provider using Google Sheets or external API (placeholder)."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(settings, 'EXTERNAL_HSN_API_KEY', None)
    
    def fetch_hsn_codes(self) -> List[Dict[str, Any]]:
        """Fetch from external source (not implemented in MVP)."""
        logger.warning("GoogleDataProvider.fetch_hsn_codes not implemented. Using mock data.")
        return MockHSNGSTProvider().fetch_hsn_codes()
    
    def fetch_gst_rates(self) -> List[Dict[str, Any]]:
        """Fetch from external source (not implemented in MVP)."""
        logger.warning("GoogleDataProvider.fetch_gst_rates not implemented. Using mock data.")
        return MockHSNGSTProvider().fetch_gst_rates()


def get_provider() -> HSNGSTProvider:
    """Factory function to get the configured provider."""
    provider_type = getattr(settings, 'HSN_GST_PROVIDER', 'mock').lower()
    
    if provider_type == 'mock':
        return MockHSNGSTProvider()
    elif provider_type == 'google':
        return GoogleDataProvider()
    else:
        logger.warning(f"Unknown provider: {provider_type}. Defaulting to mock.")
        return MockHSNGSTProvider()

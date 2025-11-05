# ai_assistant/services/gemini_service.py
import google.generativeai as genai
from django.conf import settings
import json
import time
from datetime import datetime, timedelta
from django.core.cache import cache

class GeminiService:
    def __init__(self):
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.rate_limiter = RateLimiter()
    
    def parse_command(self, user_input, context=None):
        """Parse user command and extract intent + entities"""
        
        # Check rate limits
        if not self.rate_limiter.can_make_request():
            return {
                'error': 'Rate limit exceeded. Please try again in a minute.',
                'intent': 'error',
                'entities': {}
            }
        
        # Create cache key
        cache_key = f"command_parse_{hash(user_input + str(context))}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result
        
        prompt = self._create_parsing_prompt(user_input, context)
        
        try:
            response = self.model.generate_content(prompt)
            self.rate_limiter.record_request()
            
            # Parse JSON response
            result = json.loads(response.text)
            
            # Cache for 1 hour
            cache.set(cache_key, result, timeout=3600)
            return result
            
        except json.JSONDecodeError:
            return {
                'error': 'Could not understand the command. Please try rephrasing.',
                'intent': 'error',
                'entities': {}
            }
        except Exception as e:
            return {
                'error': f'AI temporarily unavailable: {str(e)}',
                'intent': 'error', 
                'entities': {}
            }
    
    def _create_parsing_prompt(self, user_input, context):
        return f"""
        You are an ERP assistant. Parse this business command and extract intent and entities.
        
        User command: "{user_input}"
        
        Context: {json.dumps(context) if context else 'None'}
        
        Available intents:
        - create_invoice
        - add_customer
        - add_product
        - mark_payment
        - view_sales
        - view_customers
        - view_products
        - send_reminder
        - generate_report
        - search_data
        - update_customer
        - update_product
        - delete_invoice
        - general_query
        
        Extract entities like:
        - customer_name
        - product_name
        - quantity
        - price
        - amount
        - date
        - phone
        - email
        - address
        - time_period (last week, this month, etc.)
        
        Respond ONLY with valid JSON:
        {{
            "intent": "detected_intent",
            "entities": {{
                "entity_name": "entity_value"
            }},
            "confidence": 0.95,
            "clarification_needed": false,
            "clarification_question": "optional question if unclear"
        }}
        """

class RateLimiter:
    def __init__(self):
        self.requests_key = "gemini_requests_minute"
        self.daily_key = "gemini_requests_daily"
    
    def can_make_request(self):
        # Check minute limit (15 requests)
        minute_count = cache.get(self.requests_key, 0)
        if minute_count >= 15:
            return False
        
        # Check daily limit (1500 requests)
        daily_count = cache.get(self.daily_key, 0)
        if daily_count >= 1500:
            return False
        
        return True
    
    def record_request(self):
        # Increment minute counter
        minute_count = cache.get(self.requests_key, 0)
        cache.set(self.requests_key, minute_count + 1, timeout=60)
        
        # Increment daily counter
        daily_count = cache.get(self.daily_key, 0)
        cache.set(self.daily_key, daily_count + 1, timeout=86400)

# Initialize global service
gemini_service = GeminiService()
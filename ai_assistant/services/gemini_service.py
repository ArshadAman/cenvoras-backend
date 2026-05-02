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
        self.model = genai.GenerativeModel('gemini-2.5-flash')
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
            text = response.text.strip()
            if text.startswith('```json'):
                text = text[7:]
            if text.endswith('```'):
                text = text[:-3]
            result = json.loads(text.strip())
            
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
        
        Analyze this user input for a business ERP system.
        Context: {context if context else 'No context provided'}
        Input: "{user_input}"

        Possible Intents:
        - create_invoice: User wants to create a sales bill/invoice.
        - check_stock: User asking about inventory levels.
        - sales_summary: User asking for sales performance/metrics.
        - general_query: Anything else.

        If the intent is 'create_invoice', extract:
        - customer_name: The name of the customer.
        - customer_email: Any email mentioned.
        - items: A list of objects with:
            - product_name: Name of the product.
            - quantity: Number of units (default 1).
            - price: Unit price if mentioned (else null).

        Respond ONLY with valid JSON:
        {{
            "intent": "detected_intent",
            "entities": {{
                "customer_name": "...",
                "customer_email": "...",
                "items": [
                    {{"product_name": "...", "quantity": 1, "price": 500}}
                ]
            }},
            "confidence": 0.0 to 1.0,
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

def call_gemini(question, context, user=None):
    """
    Standard call to Gemini for natural language chat using the SDK.
    """
    business_name = getattr(user, 'business_name', user.username) if user else "Cenvora User"
    today_date = context.get('date', datetime.now().date().isoformat())
    
    system_prompt = (
        "You are Cenvora AI, an expert business advisor built into an ERP system. "
        "RULES:\n"
        "- NEVER greet the user or introduce yourself\n"
        "- NEVER repeat the question back\n"
        "- NEVER start with 'Hello', 'Hi', 'Great question', etc.\n"
        "- Jump STRAIGHT into the answer\n"
        "- Be concise and actionable — no fluff\n"
        "- Use markdown: **bold**, bullet points, numbered lists\n"
        "- Use ₹ for currency\n"
        "- Give specific advice based on the actual numbers in the data\n"
        "- If asked for strategy, give concrete steps, not generic advice\n\n"
        "CAPABILITIES:\n"
        "1. **Warranty lookup** — Check warranty status by invoice number, customer name, or product name\n"
        "2. **Expiring products** — List products expiring within 30 days with batch details\n"
        "3. **Sales summary** — Today, this week, this month, comparisons with last month\n"
        "4. **Business summary** — Revenue, purchases, margins, inventory value, pending payments\n"
        "5. **Monthly summary** — Month-over-month comparison with growth metrics\n"
        "6. **Create invoice guidance** — Suggest available in-stock products with prices and GST\n"
        "7. **Debit/Credit notes** — Summarize this month's notes\n"
        "8. **Stock information** — Product stock levels, low stock alerts, inventory valuation\n"
        "9. **Customer & Vendor info** — Names, emails, phones, outstanding balances\n"
        "10. **GST filing assistant** — Generate GSTR-1/GSTR-3B draft data from invoice data\n"
        "11. **AI insights** — Compare this month vs last month, identify trends, give growth advice\n\n"
        f"Business: {business_name}\n"
        f"Date: {today_date}\n\n"
        f"LIVE DATA:\n{json.dumps(context, indent=2)}"
    )

    prompt = f"{system_prompt}\n\nUser Question: {question}"
    
    try:
        response = gemini_service.model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"I'm sorry, I'm having trouble connecting to my brain right now. ({str(e)})"
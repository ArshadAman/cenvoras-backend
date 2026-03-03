"""
AI Business Assistant — Powered by Gemini 2.5 Flash.
Queries business data and sends it as context to Gemini for natural-language answers.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count, F
from datetime import timedelta
import json
import logging

logger = logging.getLogger(__name__)

GEMINI_API_KEY = getattr(settings, 'GEMINI_API_KEY', 'demo_gemini_key')


def gather_business_context(user):
    """Pull live business data to feed as context to Gemini."""
    from billing.models import SalesInvoice, SalesInvoiceItem, Customer, Payment
    from inventory.models import Product

    today = timezone.now().date()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=today.weekday())

    # Sales summaries
    sales_today = SalesInvoice.objects.filter(
        created_by=user, invoice_date=today
    ).aggregate(count=Count('id'), total=Sum('total_amount'))

    sales_week = SalesInvoice.objects.filter(
        created_by=user, invoice_date__gte=week_start
    ).aggregate(count=Count('id'), total=Sum('total_amount'))

    sales_month = SalesInvoice.objects.filter(
        created_by=user, invoice_date__gte=month_start
    ).aggregate(count=Count('id'), total=Sum('total_amount'))

    # Top products this month
    top_products = list(
        SalesInvoiceItem.objects
        .filter(sales_invoice__created_by=user, sales_invoice__invoice_date__gte=month_start)
        .values('product__name')
        .annotate(qty_sold=Sum('quantity'), revenue=Sum('amount'))
        .order_by('-qty_sold')[:5]
    )

    # Low stock
    low_stock = list(
        Product.objects.filter(created_by=user)
        .exclude(low_stock_alert=0)
        .filter(stock__lte=F('low_stock_alert'))
        .values('name', 'stock', 'low_stock_alert')[:10]
    )

    # Pending payments
    pending = list(
        Customer.objects.filter(created_by=user, current_balance__gt=0)
        .order_by('-current_balance')
        .values('name', 'current_balance')[:10]
    )
    total_receivable = Customer.objects.filter(
        created_by=user, current_balance__gt=0
    ).aggregate(total=Sum('current_balance'))['total'] or 0

    # Counts
    total_products = Product.objects.filter(created_by=user).count()
    total_customers = Customer.objects.filter(created_by=user).count()
    total_invoices = SalesInvoice.objects.filter(created_by=user).count()

    return {
        "date": str(today),
        "sales_today": {"count": sales_today['count'], "total": float(sales_today['total'] or 0)},
        "sales_this_week": {"count": sales_week['count'], "total": float(sales_week['total'] or 0)},
        "sales_this_month": {"count": sales_month['count'], "total": float(sales_month['total'] or 0)},
        "top_products_this_month": [
            {"name": p['product__name'], "qty_sold": p['qty_sold'], "revenue": float(p['revenue'])}
            for p in top_products
        ],
        "low_stock_items": [
            {"name": i['name'], "stock": i['stock'], "alert_level": i['low_stock_alert']}
            for i in low_stock
        ],
        "pending_payments": {
            "total_receivable": float(total_receivable),
            "customers": [
                {"name": c['name'], "balance": float(c['current_balance'])}
                for c in pending
            ]
        },
        "totals": {
            "products": total_products,
            "customers": total_customers,
            "invoices": total_invoices,
        }
    }


def call_gemini(question, business_context, user):
    """Call Gemini 2.5 Flash API."""
    import requests

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
        f"Business: {getattr(user, 'business_name', user.username)}\n"
        f"Date: {business_context['date']}\n\n"
        f"LIVE DATA:\n{json.dumps(business_context, indent=2)}"
    )

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\nUser question: {question}"}]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        # Extract text from Gemini response
        candidates = data.get('candidates', [])
        if candidates:
            parts = candidates[0].get('content', {}).get('parts', [])
            if parts:
                return parts[0].get('text', 'No response generated.')

        return "Sorry, I couldn't generate a response. Please try again."

    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP Error: {e}\nResponse: {e.response.text}"
        logger.error(error_msg)
        return f"⚠️ AI service unavailable. Detailed Error: {e.response.text[:200]}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Gemini API error: {e}")
        return f"⚠️ AI service unavailable. Error: {str(e)[:100]}"


class AIChatView(APIView):
    """
    POST /api/ai/chat/
    Body: {"question": "What was my top product this month?"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        question = request.data.get('question', '').strip()
        user = request.user

        if not question:
            return Response({
                "answer": "Hi! I'm **Cenvora AI** powered by Gemini. Ask me anything about your business!\n\n"
                          "Try: *\"What is my top product?\"* or *\"How are sales today?\"*"
            })

        # Gather live data
        context = gather_business_context(user)

        # Demo mode fallback
        if GEMINI_API_KEY == 'demo_gemini_key':
            return Response({
                "answer": self.demo_response(question, context),
                "question": question,
                "mode": "demo"
            })

        # Call Gemini 2.5 Flash
        answer = call_gemini(question, context, user)
        return Response({"answer": answer, "question": question, "mode": "gemini"})

    def demo_response(self, q, ctx):
        """Fallback when no Gemini key is configured."""
        q_lower = q.lower()

        if any(kw in q_lower for kw in ['top product', 'best selling', 'best product']):
            if not ctx['top_products_this_month']:
                return "No sales data this month yet."
            lines = ["**Top Products This Month:**\n"]
            for i, p in enumerate(ctx['top_products_this_month'], 1):
                lines.append(f"{i}. **{p['name']}** — {p['qty_sold']} units (₹{p['revenue']:,.2f})")
            return "\n".join(lines)

        if 'today' in q_lower and 'sale' in q_lower:
            s = ctx['sales_today']
            return f"**Sales Today:** {s['count']} invoices totaling **₹{s['total']:,.2f}**"

        if 'this month' in q_lower and 'sale' in q_lower:
            s = ctx['sales_this_month']
            return f"**Sales This Month:** {s['count']} invoices totaling **₹{s['total']:,.2f}**"

        if 'low stock' in q_lower:
            items = ctx['low_stock_items']
            if not items:
                return "✅ All products are above alert levels."
            lines = ["⚠️ **Low Stock Items:**\n"]
            for i in items:
                lines.append(f"• **{i['name']}** — {i['stock']} left (alert: {i['alert_level']})")
            return "\n".join(lines)

        if 'pending' in q_lower or 'receivable' in q_lower:
            p = ctx['pending_payments']
            if not p['customers']:
                return "✅ No pending payments!"
            lines = [f"💰 **Total Receivable: ₹{p['total_receivable']:,.2f}**\n"]
            for c in p['customers']:
                lines.append(f"• **{c['name']}** — ₹{c['balance']:,.2f}")
            return "\n".join(lines)

        return (
            "⚠️ **Demo Mode** — No Gemini API key configured.\n\n"
            "Add your key to `settings.py`:\n"
            "`GEMINI_API_KEY = 'your_key'`\n\n"
            "I can still answer: *top products, sales today/month, low stock, pending payments*"
        )

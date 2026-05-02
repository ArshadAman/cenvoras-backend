"""
AI Business Assistant — Powered by Gemini 2.5 Flash.
Queries business data and sends it as context to Gemini for natural-language answers.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count, F, Q
from datetime import timedelta
import json
import logging
from django.core.cache import cache
from subscription.services import can_use_feature
from .services.gemini_service import call_gemini
from .services.command_parser import command_parser


logger = logging.getLogger(__name__)

GEMINI_API_KEY = getattr(settings, 'GEMINI_API_KEY', 'demo_gemini_key')


def gather_business_context(user):
    """Pull comprehensive live business data to feed as context to Gemini."""
    from billing.models import SalesInvoice, SalesInvoiceItem, Customer, Payment, PurchaseBill, Vendor
    from inventory.models import Product, ProductBatch, StockPoint

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

    # Previous month for comparison
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    sales_prev_month = SalesInvoice.objects.filter(
        created_by=user, invoice_date__gte=prev_month_start, invoice_date__lte=prev_month_end
    ).aggregate(count=Count('id'), total=Sum('total_amount'))

    # Purchase summaries
    purchases_month = PurchaseBill.objects.filter(
        created_by=user, bill_date__gte=month_start
    ).aggregate(count=Count('id'), total=Sum('total_amount'))

    purchases_prev_month = PurchaseBill.objects.filter(
        created_by=user, bill_date__gte=prev_month_start, bill_date__lte=prev_month_end
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

    # In-stock products (for invoice creation suggestions)
    in_stock_products = list(
        Product.objects.filter(created_by=user, stock__gt=0)
        .values('name', 'price', 'stock', 'unit', 'hsn_sac_code', 'tax', 'warranty_months')
        .order_by('name')[:30]
    )

    # Pending payments
    pending = list(
        Customer.objects.filter(created_by=user, current_balance__gt=0)
        .order_by('-current_balance')
        .values('name', 'current_balance', 'email', 'phone')[:10]
    )
    total_receivable = Customer.objects.filter(
        created_by=user, current_balance__gt=0
    ).aggregate(total=Sum('current_balance'))['total'] or 0

    # Customer & Vendor info
    all_customers = list(
        Customer.objects.filter(created_by=user)
        .values('name', 'email', 'phone', 'current_balance')
        .order_by('name')[:20]
    )
    all_vendors = list(
        Vendor.objects.filter(created_by=user)
        .values('name', 'email', 'phone')
        .order_by('name')[:20]
    )

    # Warranty info — products with warranty sold
    warranty_products = list(
        Product.objects.filter(created_by=user, warranty_months__gt=0)
        .values('name', 'warranty_months')[:10]
    )
    # Recent warranty items from invoices
    from dateutil.relativedelta import relativedelta
    warranty_sales = []
    warranty_items_qs = SalesInvoiceItem.objects.filter(
        sales_invoice__created_by=user,
        product__warranty_months__gt=0,
    ).select_related('sales_invoice', 'product').order_by('-sales_invoice__invoice_date')[:10]
    for item in warranty_items_qs:
        end_date = item.sales_invoice.invoice_date + relativedelta(months=item.product.warranty_months)
        days_left = (end_date - today).days
        warranty_sales.append({
            'product': item.product.name,
            'customer': item.sales_invoice.customer_name or 'N/A',
            'invoice': item.sales_invoice.invoice_number,
            'warranty_months': item.product.warranty_months,
            'warranty_end': str(end_date),
            'days_left': days_left,
            'status': 'expired' if days_left < 0 else ('critical' if days_left <= 30 else ('warning' if days_left <= 90 else 'active')),
        })

    # Expiring products (next 30 days)
    cutoff = today + timedelta(days=30)
    expiring_batches = list(
        ProductBatch.objects.filter(
            product__created_by=user,
            expiry_date__isnull=False,
            expiry_date__lte=cutoff,
            is_active=True,
        ).select_related('product')
        .values('product__name', 'batch_number', 'expiry_date', 'mrp')[:10]
    )
    expiring_data = []
    for b in expiring_batches:
        days = (b['expiry_date'] - today).days
        expiring_data.append({
            'product': b['product__name'],
            'batch': b['batch_number'],
            'expiry_date': str(b['expiry_date']),
            'days_left': days,
            'mrp': float(b['mrp'] or 0),
        })

    # GST data for filing assistance
    gst_invoices_month = SalesInvoice.objects.filter(
        created_by=user, invoice_date__gte=month_start
    )
    gst_sales_total = gst_invoices_month.aggregate(total=Sum('total_amount'))['total'] or 0
    gst_tax_total = SalesInvoiceItem.objects.filter(
        sales_invoice__in=gst_invoices_month
    ).aggregate(
        total_tax=Sum('tax')
    )['total_tax'] or 0

    gst_purchases_month = PurchaseBill.objects.filter(
        created_by=user, bill_date__gte=month_start
    )
    gst_purchase_total = gst_purchases_month.aggregate(total=Sum('total_amount'))['total'] or 0

    # Credit and Debit notes
    try:
        from billing.models_returns import CreditNote, DebitNote
        credit_notes_month = CreditNote.objects.filter(
            created_by=user, date__gte=month_start
        ).aggregate(count=Count('id'), total=Sum('total_amount'))
        debit_notes_month = DebitNote.objects.filter(
            created_by=user, date__gte=month_start
        ).aggregate(count=Count('id'), total=Sum('total_amount'))
    except Exception:
        credit_notes_month = {'count': 0, 'total': 0}
        debit_notes_month = {'count': 0, 'total': 0}

    # Stock overview
    total_inventory_value = Product.objects.filter(created_by=user).aggregate(
        total=Sum(F('stock') * F('price'))
    )['total'] or 0

    # Counts
    total_products = Product.objects.filter(created_by=user).count()
    total_customers = Customer.objects.filter(created_by=user).count()
    total_invoices = SalesInvoice.objects.filter(created_by=user).count()
    total_vendors = Vendor.objects.filter(created_by=user).count()

    return {
        "date": str(today),
        # Sales
        "sales_today": {"count": sales_today['count'], "total": float(sales_today['total'] or 0)},
        "sales_this_week": {"count": sales_week['count'], "total": float(sales_week['total'] or 0)},
        "sales_this_month": {"count": sales_month['count'], "total": float(sales_month['total'] or 0)},
        "sales_last_month": {"count": sales_prev_month['count'], "total": float(sales_prev_month['total'] or 0)},
        # Purchases
        "purchases_this_month": {"count": purchases_month['count'], "total": float(purchases_month['total'] or 0)},
        "purchases_last_month": {"count": purchases_prev_month['count'], "total": float(purchases_prev_month['total'] or 0)},
        # Products
        "top_products_this_month": [
            {"name": p['product__name'], "qty_sold": p['qty_sold'], "revenue": float(p['revenue'])}
            for p in top_products
        ],
        "low_stock_items": [
            {"name": i['name'], "stock": i['stock'], "alert_level": i['low_stock_alert']}
            for i in low_stock
        ],
        "in_stock_products": [
            {"name": p['name'], "price": float(p['price']), "stock": p['stock'],
             "unit": p['unit'], "hsn": p['hsn_sac_code'], "gst_percent": float(p['tax']),
             "warranty_months": p['warranty_months']}
            for p in in_stock_products
        ],
        # Payments
        "pending_payments": {
            "total_receivable": float(total_receivable),
            "customers": [
                {"name": c['name'], "balance": float(c['current_balance']),
                 "email": c.get('email', ''), "phone": c.get('phone', '')}
                for c in pending
            ]
        },
        # People
        "customers": [
            {"name": c['name'], "email": c.get('email', ''), "phone": c.get('phone', ''),
             "balance": float(c.get('current_balance', 0) or 0)}
            for c in all_customers
        ],
        "vendors": [
            {"name": v['name'], "email": v.get('email', ''), "phone": v.get('phone', '')}
            for v in all_vendors
        ],
        # Warranty
        "warranty_products": warranty_products,
        "recent_warranty_sales": warranty_sales,
        # Expiry
        "expiring_products_30d": expiring_data,
        # GST
        "gst_this_month": {
            "sales_total": float(gst_sales_total),
            "tax_collected": float(gst_tax_total),
            "purchase_total": float(gst_purchase_total),
            "net_gst_payable": float(gst_tax_total),  # Simplified
        },
        # Credit/Debit Notes
        "credit_notes_this_month": {"count": credit_notes_month['count'] or 0, "total": float(credit_notes_month['total'] or 0)},
        "debit_notes_this_month": {"count": debit_notes_month['count'] or 0, "total": float(debit_notes_month['total'] or 0)},
        # Inventory overview
        "total_inventory_value": float(total_inventory_value),
        # Counts
        "totals": {
            "products": total_products,
            "customers": total_customers,
            "invoices": total_invoices,
            "vendors": total_vendors,
        }
    }



# The call_gemini function has been moved to services/gemini_service.py
# and is imported at the top of this file.



class AIChatView(APIView):
    """
    POST /api/ai/chat/
    Body: {"question": "What was my top product this month?"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        question = request.data.get('question', '').strip()
        user = request.user

        if not can_use_feature(user, 'ai_copilot'):
            return Response({
                "detail": "Gemini chat is available only on the Business plan.",
            }, status=403)

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

        # Check for active interactive session
        session_key = f"ai_chat_state_{user.id}"
        session_state = cache.get(session_key)

        # 1. Get business context
        context = gather_business_context(user)
        
        # 2. Get AI Response
        answer = call_gemini(question, context, user)
        
        # 3. Process Intent / State
        action = None
        
        # If we have an active session, process the selection
        if session_state and session_state.get('step') == 'SELECT_CUSTOMER':
            # Check if input matches one of the options or a name
            # For simplicity, if it's not 'Create New', we'll try to find the match
            if question.lower() == 'create new customer' or 'new' in question.lower():
                # Proceed to draft or ask more questions
                cache.delete(session_key)
            else:
                from .services.invoice_service import search_customers, create_invoice_from_ai
                matches = search_customers(user, question)
                if matches.count() == 1:
                    entities = session_state.get('entities', {})
                    entities['customer_name'] = matches[0].name
                    entities['customer_id'] = str(matches[0].id)
                    cache.delete(session_key)
                    # Proceed to create
                    result = create_invoice_from_ai(user, entities, request=self.request)
                    if result['status'] == 'success':
                        action = {
                            "intent": "create_invoice",
                            "status": "success",
                            "invoice_id": result['invoice_id'],
                            "invoice_number": result['invoice_number']
                        }
                        answer = f"✅ **Invoice Created!**\n\nI've recorded bill **{result['invoice_number']}** for **{result['customer_name']}**."
                        return Response({"answer": answer, "action": action})

        # Parse intent
        parsed = command_parser.parse(question, context)
        
        if parsed and parsed.get('intent') == 'create_invoice':
            entities = parsed.get('entities', {})
            customer_name = entities.get('customer_name')
            
            if customer_name:
                from .services.invoice_service import search_customers, create_invoice_from_ai
                matches = search_customers(user, customer_name)
                
                if matches.count() > 1:
                    # Multiple matches found, ask user to select
                    options = [{"id": str(m.id), "name": m.name, "detail": m.address or m.phone or "No details"} for m in matches]
                    options.append({"id": "new", "name": "Create New Customer", "detail": f"Add '{customer_name}' as a new record"})
                    
                    action = {
                        "intent": "select_option",
                        "type": "customer",
                        "options": options,
                        "entities": entities
                    }
                    answer = f"I found multiple customers matching **{customer_name}**. Which one should I use for this bill?"
                    
                    # Store state in cache
                    cache.set(session_key, {"step": "SELECT_CUSTOMER", "entities": entities}, timeout=600)
                elif matches.count() == 1:
                    # One match found, check items
                    customer = matches[0]
                    entities['customer_name'] = customer.name
                    entities['customer_id'] = str(customer.id)
                    
                    # Proceed to direct creation or next check (Items)
                    # For now, let's stick to the direct creation if it's high confidence
                    if parsed.get('confidence', 0) > 0.8:
                        result = create_invoice_from_ai(user, entities, request=self.request)
                        if result['status'] == 'success':
                            action = {
                                "intent": "create_invoice",
                                "status": "success",
                                "invoice_id": result['invoice_id'],
                                "invoice_number": result['invoice_number']
                            }
                            answer = f"✅ **Invoice Created Successfully!**\n\nI've created invoice **{result['invoice_number']}** for **{result['customer_name']}** totaling **₹{result['total_amount']:,.2f}**."
                        else:
                            action = parsed
                            action['status'] = 'draft'
                    else:
                        action = parsed
                        action['status'] = 'draft'
                else:
                    # No matches found, ask to create new or proceed as draft
                    answer = f"I couldn't find a customer named **{customer_name}**. Should I create a new record for them or prepare a draft?"
                    action = {
                        "intent": "select_option",
                        "type": "customer_not_found",
                        "options": [
                            {"id": "new", "name": "Create New Customer", "detail": "Add to your records"},
                            {"id": "draft", "name": "Prepare Draft", "detail": "I'll open the form for you"}
                        ],
                        "entities": entities
                    }
            else:
                # No customer name mentioned
                action = parsed
                action['status'] = 'draft'
        elif parsed and parsed.get('intent') != 'general_query' and parsed.get('confidence', 0) > 0.6:
            action = parsed

        return Response({
            "answer": answer, 
            "question": question, 
            "mode": "gemini",
            "action": action
        })

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

        if 'warranty' in q_lower:
            items = ctx.get('recent_warranty_sales', [])
            if not items:
                return "No warranty records found. Set warranty duration on products and create sales invoices."
            lines = ["🛡️ **Recent Warranty Items:**\n"]
            for w in items:
                emoji = "🟢" if w['status'] == 'active' else ("🟡" if w['status'] == 'warning' else ("🔴" if w['status'] == 'critical' else "⚫"))
                lines.append(f"{emoji} **{w['product']}** → {w['customer']} | Invoice: {w['invoice']} | Ends: {w['warranty_end']} ({w['days_left']}d left)")
            return "\n".join(lines)

        if 'expir' in q_lower:
            items = ctx.get('expiring_products_30d', [])
            if not items:
                return "✅ No products expiring in the next 30 days."
            lines = ["⏰ **Products Expiring Within 30 Days:**\n"]
            for e in items:
                lines.append(f"• **{e['product']}** (Batch: {e['batch']}) — Expires: {e['expiry_date']} ({e['days_left']}d left)")
            return "\n".join(lines)

        if 'gst' in q_lower or 'gstr' in q_lower:
            g = ctx.get('gst_this_month', {})
            return (
                f"📊 **GST Summary This Month:**\n\n"
                f"• Sales Total: **₹{g.get('sales_total', 0):,.2f}**\n"
                f"• Tax Collected (Output): **₹{g.get('tax_collected', 0):,.2f}**\n"
                f"• Purchases Total: **₹{g.get('purchase_total', 0):,.2f}**\n"
                f"• Net GST Payable: **₹{g.get('net_gst_payable', 0):,.2f}**\n\n"
                f"*Use this data for GSTR-1 and GSTR-3B filing.*"
            )

        if 'customer' in q_lower and ('list' in q_lower or 'info' in q_lower or 'all' in q_lower):
            customers = ctx.get('customers', [])
            if not customers:
                return "No customers found."
            lines = ["👥 **Customers:**\n"]
            for c in customers[:10]:
                lines.append(f"• **{c['name']}** | {c.get('email','—')} | {c.get('phone','—')} | Balance: ₹{c['balance']:,.2f}")
            return "\n".join(lines)

        if 'vendor' in q_lower and ('list' in q_lower or 'info' in q_lower or 'all' in q_lower):
            vendors = ctx.get('vendors', [])
            if not vendors:
                return "No vendors found."
            lines = ["🏭 **Vendors:**\n"]
            for v in vendors[:10]:
                lines.append(f"• **{v['name']}** | {v.get('email','—')} | {v.get('phone','—')}")
            return "\n".join(lines)

        if 'summary' in q_lower or 'business' in q_lower or 'overview' in q_lower:
            s = ctx['sales_this_month']
            p = ctx['purchases_this_month']
            total_inv = ctx['total_inventory_value']
            receivable = ctx['pending_payments']['total_receivable']
            return (
                f"📈 **Business Summary:**\n\n"
                f"• Sales This Month: **₹{s['total']:,.2f}** ({s['count']} invoices)\n"
                f"• Purchases This Month: **₹{p['total']:,.2f}** ({p['count']} bills)\n"
                f"• Inventory Value: **₹{total_inv:,.2f}**\n"
                f"• Accounts Receivable: **₹{receivable:,.2f}**\n"
                f"• Products: {ctx['totals']['products']} | Customers: {ctx['totals']['customers']} | Vendors: {ctx['totals']['vendors']}"
            )

        if 'create invoice' in q_lower or 'make invoice' in q_lower or 'new invoice' in q_lower:
            products = ctx.get('in_stock_products', [])
            if not products:
                return "No in-stock products available."
            lines = ["🧾 **Available Products for Invoice:**\n\n| Product | Price | Stock | GST | Unit |\n|---|---|---|---|---|"]
            for p in products[:15]:
                lines.append(f"| {p['name']} | ₹{p['price']:.2f} | {p['stock']} | {p['gst_percent']}% | {p['unit']} |")
            lines.append("\n*Go to Sales → New Invoice to create one.*")
            return "\n".join(lines)

        if 'credit note' in q_lower:
            cn = ctx.get('credit_notes_this_month', {})
            return f"📝 **Credit Notes This Month:** {cn.get('count', 0)} notes totaling **₹{cn.get('total', 0):,.2f}**"

        if 'debit note' in q_lower:
            dn = ctx.get('debit_notes_this_month', {})
            return f"📝 **Debit Notes This Month:** {dn.get('count', 0)} notes totaling **₹{dn.get('total', 0):,.2f}**"

        if 'stock' in q_lower or 'inventory' in q_lower:
            return (
                f"📦 **Stock Overview:**\n\n"
                f"• Total Products: **{ctx['totals']['products']}**\n"
                f"• Inventory Value: **₹{ctx['total_inventory_value']:,.2f}**\n"
                f"• Low Stock Alerts: **{len(ctx['low_stock_items'])}** items"
            )

        return (
            "⚠️ **Demo Mode** — No Gemini API key configured.\n\n"
            "Add your key to the `.env` file:\n"
            "`GEMINI_API_KEY = 'your_key'`\n\n"
            "I can answer: *sales, purchases, warranty, expiry, GST, stock, customers, vendors, business summary, create invoice, credit/debit notes*"
        )

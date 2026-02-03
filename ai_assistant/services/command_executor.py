# ai_assistant/services/command_executor.py
from typing import Dict, Any
from ..commands import (
    invoice_commands,
    customer_commands,
    inventory_commands,
    payment_commands,
    analytics_commands
)

class CommandExecutor:
    def __init__(self):
        self.handlers = {
            'create_invoice': invoice_commands.CreateInvoiceHandler(),
            'add_customer': customer_commands.AddCustomerHandler(),
            'add_product': inventory_commands.AddProductHandler(),
            'mark_payment': payment_commands.MarkPaymentHandler(),
            'view_sales': analytics_commands.ViewSalesHandler(),
            'view_customers': customer_commands.ViewCustomersHandler(),
            'search_data': analytics_commands.SearchDataHandler(),
            'general_query': analytics_commands.GeneralQueryHandler(),
        }
    
    def execute(self, intent: str, entities: Dict[str, Any], user) -> Dict[str, Any]:
        """Execute a parsed command"""
        try:
            if intent not in self.handlers:
                return {
                    'success': False,
                    'error': f'Unknown command: {intent}',
                    'message': 'I don\'t know how to handle that request yet.'
                }
            
            handler = self.handlers[intent]
            result = handler.execute(entities, user)
            
            return {
                'success': True,
                'intent': intent,
                'result': result,
                'message': result.get('message', 'Command executed successfully!')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Sorry, something went wrong while processing your request.'
            }

command_executor = CommandExecutor()
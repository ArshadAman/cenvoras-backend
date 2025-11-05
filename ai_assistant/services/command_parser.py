# ai_assistant/services/command_parser.py
from .gemini_service import gemini_service
import re
from datetime import datetime, timedelta

class CommandParser:
    def __init__(self):
        self.gemini = gemini_service
    
    def parse(self, user_input, context=None):
        """Parse user command using Gemini AI"""
        return self.gemini.parse_command(user_input, context)
    
    def preprocess_input(self, user_input):
        """Clean and preprocess user input"""
        # Convert to lowercase
        text = user_input.lower().strip()
        
        # Handle common abbreviations
        replacements = {
            'inv': 'invoice',
            'cust': 'customer',
            'prod': 'product',
            'qty': 'quantity',
            'amt': 'amount',
            'k': '000',  # 50k -> 50000
            'lakh': '00000',  # 5 lakh -> 500000
            'cr': '0000000',  # 2 cr -> 20000000
        }
        
        for abbrev, full in replacements.items():
            text = re.sub(rf'\b{abbrev}\b', full, text)
        
        return text
    
    def extract_numbers(self, text):
        """Extract numbers from text"""
        # Find all numbers (including decimals)
        numbers = re.findall(r'\d+\.?\d*', text)
        return [float(n) if '.' in n else int(n) for n in numbers]
    
    def extract_dates(self, text):
        """Extract date references from text"""
        today = datetime.now().date()
        
        if 'today' in text:
            return today
        elif 'yesterday' in text:
            return today - timedelta(days=1)
        elif 'last week' in text:
            return today - timedelta(weeks=1)
        elif 'last month' in text:
            return today - timedelta(days=30)
        
        # TODO: Add more sophisticated date parsing
        return None

command_parser = CommandParser()
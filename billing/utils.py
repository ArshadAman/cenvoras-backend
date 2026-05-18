import decimal

def calculate_invoice_totals(shop_profile, subtotal):
    """
    Calculates the tax amount and grand total based on the shop's active region and tax settings.
    Ensure subtotal is a decimal.Decimal type for precise financial calculation.
    
    Returns:
        dict: {
            'subtotal': Decimal,
            'tax_amount': Decimal,
            'tax_type': str (e.g., 'VAT (5%)' or 'GST (18%)'),
            'grand_total': Decimal
        }
    """
    subtotal_dec = decimal.Decimal(str(subtotal))
    tax_amount = decimal.Decimal('0.00')
    tax_type = "No Tax"
    
    country = getattr(shop_profile, 'country', 'IN')
    
    if country == 'AE':
        is_vat_registered = getattr(shop_profile, 'is_vat_registered', False)
        if is_vat_registered:
            tax_type = "VAT (5%)"
            # UAE VAT is flat 5%
            tax_amount = (subtotal_dec * decimal.Decimal('0.05')).quantize(decimal.Decimal('0.01'))
    else:
        # Default to India logic
        gstin = getattr(shop_profile, 'gstin', None)
        if gstin:
            tax_type = "GST (18%)"
            # Placeholder for standard Indian GST (18%)
            tax_amount = (subtotal_dec * decimal.Decimal('0.18')).quantize(decimal.Decimal('0.01'))
            
    grand_total = subtotal_dec + tax_amount
    
    return {
        'subtotal': subtotal_dec,
        'tax_amount': tax_amount,
        'tax_type': tax_type,
        'grand_total': grand_total
    }

from django.db import models
from django.conf import settings
import uuid
from .models import Product

class PriceList(models.Model):
    """
    Feature 58: Trade Discount / Specific Pricing.
    Can be assigned to a Customer (Party Category) to override standard prices.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="e.g. Wholesale 2024")
    currency = models.CharField(max_length=10, default='INR')
    
    # Target
    PARTY_CATEGORY_CHOICES = [
        ('retailer', 'Retailer'),
        ('wholesaler', 'Wholesaler'),
        ('distributor', 'Distributor'),
        ('consumer', 'End Consumer'),
    ]
    party_category = models.CharField(
        max_length=20, 
        choices=PARTY_CATEGORY_CHOICES, 
        blank=True, null=True,
        help_text="Applies to all customers in this category"
    )
    
    is_active = models.BooleanField(default=True)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class PriceListItem(models.Model):
    price_list = models.ForeignKey(PriceList, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Override Price")
    min_qty = models.PositiveIntegerField(default=1, help_text="Min Qty for this price")
    
    class Meta:
        unique_together = ('price_list', 'product', 'min_qty')

class Scheme(models.Model):
    """
    Feature 59: Schemes (Buy X Get Y, Flat Discount, etc.)
    """
    SCHEME_TYPE_CHOICES = [
        ('bogo', 'Buy X Get Y (Free)'),
        ('flat_discount', 'Flat Discount Amount'),
        ('percentage_discount', 'Percentage Discount'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="e.g. Diwali Bonanza")
    scheme_type = models.CharField(max_length=30, choices=SCHEME_TYPE_CHOICES)
    
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    
    # Target (Rule conditions)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='schemes_active', help_text="Product on which scheme is applied")
    min_qty = models.PositiveIntegerField(default=1, help_text="Min Qty to trigger scheme")
    
    # Benefit
    free_product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='schemes_as_free', help_text="For BOGO")
    free_qty = models.PositiveIntegerField(default=0, help_text="Get Y Free")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    
    def __str__(self):
        return f"{self.name} ({self.get_scheme_type_display()})"

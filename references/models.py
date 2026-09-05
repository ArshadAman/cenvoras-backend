from django.db import models
from django.utils.text import slugify


class HSNCode(models.Model):
    """HSN (Harmonized System of Nomenclature) codes for Indian products."""
    code = models.CharField(max_length=8, unique=True, db_index=True)  # e.g., "8471"
    description = models.CharField(max_length=500)  # e.g., "Automatic Data Processing Machines"
    category = models.CharField(max_length=100, blank=True)  # e.g., "Electronics", "Textiles"
    slug = models.SlugField(max_length=150, unique=True, db_index=True)  # For URL: "8471-automatic-data-processing-machines"
    
    # SEO metadata
    meta_title = models.CharField(max_length=200, blank=True)  # Custom SEO title
    meta_description = models.CharField(max_length=300, blank=True)  # Custom meta description
    long_description = models.TextField(blank=True)  # Detailed content for the page
    
    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    external_id = models.CharField(max_length=100, blank=True, null=True, help_text="ID from external API source")
    
    class Meta:
        verbose_name = "HSN Code"
        verbose_name_plural = "HSN Codes"
        ordering = ['code']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['slug']),
            models.Index(fields=['category']),
        ]
    
    def __str__(self):
        return f"HSN {self.code} - {self.description}"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.code}-{self.description}")[:150]
        if not self.meta_title:
            self.meta_title = f"HSN Code {self.code}: {self.description}"
        if not self.meta_description:
            self.meta_description = f"Complete information about HSN Code {self.code} ({self.description}). Used for GST classification in India."
        super().save(*args, **kwargs)


class GSTRate(models.Model):
    """GST rates for different product categories and HSN codes."""
    GST_RATE_CHOICES = [
        (0, '0%'),
        (5, '5%'),
        (12, '12%'),
        (18, '18%'),
        (28, '28%'),
    ]
    
    category = models.CharField(max_length=200, db_index=True)  # e.g., "Electronics and Accessories"
    hsn_codes = models.CharField(max_length=500, blank=True, help_text="Comma-separated HSN codes")  # e.g., "8471,8517,9030"
    rate = models.IntegerField(choices=GST_RATE_CHOICES)  # 5, 12, 18, 28
    slug = models.SlugField(max_length=150, unique=True, db_index=True)  # For URL: "gst-18-electronics"
    
    # SEO metadata
    meta_title = models.CharField(max_length=200, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)
    long_description = models.TextField(blank=True)  # Detailed content
    
    # Additional info
    effective_from = models.DateField(blank=True, null=True)  # When this rate became effective
    notes = models.TextField(blank=True)  # Any exemptions, special cases, etc.
    
    # Tracking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    external_id = models.CharField(max_length=100, blank=True, null=True)
    
    class Meta:
        verbose_name = "GST Rate"
        verbose_name_plural = "GST Rates"
        ordering = ['rate', 'category']
        unique_together = [('category', 'rate')]
        indexes = [
            models.Index(fields=['rate']),
            models.Index(fields=['category']),
            models.Index(fields=['slug']),
        ]
    
    def __str__(self):
        return f"GST {self.rate}% - {self.category}"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"gst-{self.rate}-{self.category}")[:150]
        if not self.meta_title:
            self.meta_title = f"GST Rate {self.rate}% for {self.category} in India 2026"
        if not self.meta_description:
            self.meta_description = f"Current GST rate of {self.rate}% for {self.category}. Applicable HSN codes: {self.hsn_codes}. Updated for 2026."
        super().save(*args, **kwargs)
    
    def get_hsn_codes_list(self):
        """Return HSN codes as a list."""
        if not self.hsn_codes:
            return []
        return [code.strip() for code in self.hsn_codes.split(',')]

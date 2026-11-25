"""
Model definition for CA Mapping Profile & Auditor Access.
Persists custom Tally Chart of Accounts mappings and CA Portal magic-link credentials.
"""
from datetime import timedelta
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CAPresetChoices(models.TextChoices):
    STANDARD_TALLY = 'standard_tally', 'Standard TallyPrime Defaults'
    DETAILED_SLABS = 'detailed_slabs', 'Detailed Slab-wise Accounts'
    SIMPLIFIED_GST = 'simplified_gst', 'Simplified Single Tax Ledgers'


PRESET_CONFIGURATIONS = {
    CAPresetChoices.STANDARD_TALLY: {
        'sales_account': 'Sales Account',
        'interstate_sales_account': 'Interstate Sales Account',
        'purchase_account': 'Purchase Account',
        'interstate_purchase_account': 'Interstate Purchase Account',
        'sales_return_account': 'Sales Returns',
        'purchase_return_account': 'Purchase Returns',
        'cgst_output_prefix': 'Output CGST',
        'sgst_output_prefix': 'Output SGST',
        'igst_output_prefix': 'Output IGST',
        'cgst_input_prefix': 'Input CGST',
        'sgst_input_prefix': 'Input SGST',
        'igst_input_prefix': 'Input IGST',
        'round_off_account': 'Round Off',
        'split_tax_by_rate': True,
        'separate_interstate_sales': False,
        'separate_interstate_purchases': False,
    },
    CAPresetChoices.DETAILED_SLABS: {
        'sales_account': 'Local Sales',
        'interstate_sales_account': 'Interstate Sales',
        'purchase_account': 'Local Purchases',
        'interstate_purchase_account': 'Interstate Purchases',
        'sales_return_account': 'Sales Returns',
        'purchase_return_account': 'Purchase Returns',
        'cgst_output_prefix': 'Output CGST',
        'sgst_output_prefix': 'Output SGST',
        'igst_output_prefix': 'Output IGST',
        'cgst_input_prefix': 'Input CGST',
        'sgst_input_prefix': 'Input SGST',
        'igst_input_prefix': 'Input IGST',
        'round_off_account': 'Rounding Adjustment',
        'split_tax_by_rate': True,
        'separate_interstate_sales': True,
        'separate_interstate_purchases': True,
    },
    CAPresetChoices.SIMPLIFIED_GST: {
        'sales_account': 'Sales',
        'interstate_sales_account': 'Sales',
        'purchase_account': 'Purchases',
        'interstate_purchase_account': 'Purchases',
        'sales_return_account': 'Sales Return',
        'purchase_return_account': 'Purchase Return',
        'cgst_output_prefix': 'Output CGST',
        'sgst_output_prefix': 'Output SGST',
        'igst_output_prefix': 'Output IGST',
        'cgst_input_prefix': 'Input CGST',
        'sgst_input_prefix': 'Input SGST',
        'igst_input_prefix': 'Input IGST',
        'round_off_account': 'Round Off',
        'split_tax_by_rate': False,  # No % in tax ledger name
        'separate_interstate_sales': False,
        'separate_interstate_purchases': False,
    },
}


class CAMappingProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ca_mapping_profile',
    )

    # CA Contact & Audit Credentials
    ca_name = models.CharField(max_length=255, blank=True, default='')
    ca_email = models.EmailField(blank=True, null=True)
    ca_phone = models.CharField(max_length=20, blank=True, null=True)
    ca_firm_name = models.CharField(max_length=255, blank=True, default='')
    magic_link_token = models.CharField(max_length=64, blank=True, null=True, unique=True)
    magic_link_expires_at = models.DateTimeField(null=True, blank=True)

    # Active Mapping Preset
    preset = models.CharField(
        max_length=32,
        choices=CAPresetChoices.choices,
        default=CAPresetChoices.STANDARD_TALLY,
    )

    # Chart of Accounts Ledger Names
    sales_account = models.CharField(max_length=150, default='Sales Account')
    interstate_sales_account = models.CharField(max_length=150, default='Interstate Sales Account')
    purchase_account = models.CharField(max_length=150, default='Purchase Account')
    interstate_purchase_account = models.CharField(max_length=150, default='Interstate Purchase Account')
    sales_return_account = models.CharField(max_length=150, default='Sales Returns')
    purchase_return_account = models.CharField(max_length=150, default='Purchase Returns')

    cgst_output_prefix = models.CharField(max_length=100, default='Output CGST')
    sgst_output_prefix = models.CharField(max_length=100, default='Output SGST')
    igst_output_prefix = models.CharField(max_length=100, default='Output IGST')
    cgst_input_prefix = models.CharField(max_length=100, default='Input CGST')
    sgst_input_prefix = models.CharField(max_length=100, default='Input SGST')
    igst_input_prefix = models.CharField(max_length=100, default='Input IGST')

    round_off_account = models.CharField(max_length=100, default='Round Off')

    split_tax_by_rate = models.BooleanField(
        default=True,
        help_text="If True, emits Output CGST 9%, Output SGST 9%. If False, emits Output CGST.",
    )
    separate_interstate_sales = models.BooleanField(
        default=False,
        help_text="If True, routes IGST sales to interstate_sales_account.",
    )
    separate_interstate_purchases = models.BooleanField(
        default=False,
        help_text="If True, routes IGST purchases to interstate_purchase_account.",
    )

    # Freeform overrides e.g. {"Output CGST 9%": "CGST @ 9% Ledger"}
    custom_ledger_overrides = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def apply_preset(self, preset_name: str, save: bool = True):
        """Apply a predefined configuration preset to this profile."""
        if preset_name not in PRESET_CONFIGURATIONS:
            raise ValueError(f"Unknown preset: {preset_name}")
        config = PRESET_CONFIGURATIONS[preset_name]
        self.preset = preset_name
        for field, val in config.items():
            setattr(self, field, val)
        if save:
            self.save()

    def generate_magic_link_token(self, days: int = 30) -> str:
        """Generate a cryptographically secure URL-safe token for passwordless CA portal access."""
        token = secrets.token_urlsafe(32)
        self.magic_link_token = token
        self.magic_link_expires_at = timezone.now() + timedelta(days=days)
        self.save(update_fields=['magic_link_token', 'magic_link_expires_at'])
        return token

    def is_magic_link_valid(self) -> bool:
        if not self.magic_link_token or not self.magic_link_expires_at:
            return False
        return timezone.now() < self.magic_link_expires_at

    def to_mapping_dict(self) -> dict:
        """Convert profile into dictionary expected by TallyXMLExporter."""
        mapping = {
            'sales_account': self.sales_account,
            'interstate_sales_account': self.interstate_sales_account,
            'purchase_account': self.purchase_account,
            'interstate_purchase_account': self.interstate_purchase_account,
            'sales_return_account': self.sales_return_account,
            'purchase_return_account': self.purchase_return_account,
            'cgst_output_prefix': self.cgst_output_prefix,
            'sgst_output_prefix': self.sgst_output_prefix,
            'igst_output_prefix': self.igst_output_prefix,
            'cgst_input_prefix': self.cgst_input_prefix,
            'sgst_input_prefix': self.sgst_input_prefix,
            'igst_input_prefix': self.igst_input_prefix,
            'round_off_account': self.round_off_account,
            'split_tax_by_rate': self.split_tax_by_rate,
            'separate_interstate_sales': self.separate_interstate_sales,
            'separate_interstate_purchases': self.separate_interstate_purchases,
        }
        if self.custom_ledger_overrides and isinstance(self.custom_ledger_overrides, dict):
            mapping.update(self.custom_ledger_overrides)
        return mapping

    def __str__(self):
        biz = getattr(self.tenant, 'business_name', '') or str(self.tenant)
        return f"CA Profile ({biz}) - Preset: {self.preset}"

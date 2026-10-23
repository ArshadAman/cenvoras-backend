from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0051_rename_tenantlearnedhsngst_index'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='purchasebill',
            constraint=models.UniqueConstraint(fields=('created_by', 'bill_number'), name='uniq_bill_number_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.UniqueConstraint(fields=('created_by', 'po_number'), name='uniq_po_number_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='salesorder',
            constraint=models.UniqueConstraint(fields=('created_by', 'order_number'), name='uniq_order_number_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='deliverychallan',
            constraint=models.UniqueConstraint(fields=('created_by', 'challan_number'), name='uniq_challan_number_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='quotation',
            constraint=models.UniqueConstraint(fields=('created_by', 'quotation_number'), name='uniq_quotation_number_per_tenant'),
        ),
    ]

# Generated for HRMS enhancements: Contract fee, Allowance calculation mode, KYC documents, Offer letter settings

from django.db import migrations, models
import hr.models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0012_hrmssettings_weekend_rule_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='contract_fee',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Agreed fee/remuneration for contract personnel (exempt from standard statutory payroll).',
                max_digits=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='employeeallowancebonus',
            name='calculation_mode',
            field=models.CharField(
                choices=[('fixed', 'Fixed Flat Amount'), ('per_day', 'Daily Rate (Per Day × Days Present)')],
                default='fixed',
                help_text='Determines whether this allowance is paid flat or multiplied by attendance days present.',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='hrdocument',
            name='document_type',
            field=models.CharField(
                choices=[
                    ('offer_letter', 'Offer Letter'),
                    ('appointment_letter', 'Appointment Letter'),
                    ('aadhaar', 'Aadhaar Card (KYC)'),
                    ('pan', 'PAN Card (KYC)'),
                    ('marksheet_10th', '10th Marksheet / Education Certificate (KYC)'),
                    ('id_proof', 'Other ID Proof'),
                    ('tax_form', 'Tax Declaration Form'),
                    ('resignation', 'Resignation Letter'),
                    ('appraisal', 'Appraisal Letter'),
                    ('other', 'Other Document'),
                ],
                default='other',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='hrdocument',
            name='file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=hr.models.tenant_kyc_upload_path,
            ),
        ),
        migrations.AddField(
            model_name='hrmssettings',
            name='auto_send_offer_letter',
            field=models.BooleanField(
                default=False,
                help_text='Automatically email an official offer letter to newly onboarded employees from the company.',
            ),
        ),
    ]

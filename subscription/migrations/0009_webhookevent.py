# Generated migration for WebhookEvent model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0008_subscriptionpayment_action_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebhookEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_id', models.CharField(help_text='Unique event ID from Cashfree webhook', max_length=200, unique=True)),
                ('provider', models.CharField(default='cashfree', max_length=30)),
                ('event_type', models.CharField(help_text='e.g., PAYMENT_SUCCESS, PAYMENT_FAILED', max_length=100)),
                ('order_id', models.CharField(blank=True, max_length=64, null=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('processed', models.BooleanField(default=False, help_text='Whether this event has been processed')),
                ('error_message', models.TextField(blank=True, help_text='Error during processing, if any', null=True)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-received_at'],
            },
        ),
        migrations.AddIndex(
            model_name='webhookevent',
            index=models.Index(fields=['event_id'], name='subscription_event_i_8c3d9e_idx'),
        ),
        migrations.AddIndex(
            model_name='webhookevent',
            index=models.Index(fields=['order_id'], name='subscription_order_i_9f4a2c_idx'),
        ),
        migrations.AddIndex(
            model_name='webhookevent',
            index=models.Index(fields=['processed'], name='subscription_process_5b1e7d_idx'),
        ),
    ]

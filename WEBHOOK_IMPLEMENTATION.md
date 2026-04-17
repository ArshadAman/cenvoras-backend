# Cashfree Webhook Implementation & Payment Processing

## Overview
This document describes the webhook system for handling Cashfree payment events asynchronously via Celery.

## Architecture

### Payment Flow (Synchronous + Webhook Verification)

```
User → Cashfree Payment Modal → Payment Success/Failure
                                        ↓
                            Cashfree Webhook (POST)
                                        ↓
                       /subscription/webhooks/cashfree/
                                        ↓
                            Signature Verification
                                        ↓
                            Idempotency Check (WebhookEvent)
                                        ↓
                    Queue Celery Task: process_cashfree_webhook
                                        ↓
                            Return 200 OK to Cashfree
                                        ↓
                        [Async] Process Payment
                                        ↓
                    [Async] Update Subscription
                                        ↓
                    [Async] Send Confirmation Email
```

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Webhook Configuration
CASHFREE_WEBHOOK_SECRET=your_webhook_secret_from_cashfree
CASHFREE_WEBHOOK_URL=https://api.cenvora.app/subscription/webhooks/cashfree/

# Email Configuration (already configured)
TRANSACTIONAL_EMAIL_API_KEY=your_ahasend_api_key
TRANSACTIONAL_EMAIL_SENDER_EMAIL=noreply@cenvora.app
TRANSACTIONAL_EMAIL_SENDER_NAME=Cenvora
TRANSACTIONAL_EMAIL_API_URL=https://api.ahasend.com/v1

# Celery Configuration (if not already set)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

### Cashfree Dashboard Setup

1. Log in to Cashfree Dashboard
2. Navigate to Settings → Webhooks
3. Add webhook endpoint:
   - **URL**: `https://api.cenvora.app/subscription/webhooks/cashfree/`
   - **Events**: `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `PAYMENT_PENDING`
4. Copy the Webhook Secret and set in env as `CASHFREE_WEBHOOK_SECRET`

## Files Modified/Created

### 1. **Models** (`subscription/models.py`)
- **WebhookEvent** - Tracks incoming webhooks for idempotency
  - `event_id` - Unique webhook ID from Cashfree
  - `event_type` - PAYMENT_SUCCESS, PAYMENT_FAILED, etc.
  - `processed` - Whether webhook has been processed
  - `payload` - Raw webhook data

### 2. **Views** (`subscription/views.py`)
- **cashfree_webhook()** - Webhook endpoint
  - Verifies Cashfree signature
  - Checks for duplicates (idempotency)
  - Queues async processing with Celery
  - Returns 200 OK immediately to Cashfree

### 3. **Celery Tasks** (`subscription/tasks.py`)
- **process_cashfree_webhook()** - Main async task
  - Handles PAYMENT_SUCCESS, PAYMENT_FAILED, PAYMENT_PENDING events
  - Updates subscription state
  - Handles instant upgrades, renewals, activation
  - Queues email notifications

- **send_payment_confirmation_email()** - Async email task
  - Sends confirmation email from noreply@cenvora.app
  - Includes plan, amount, order ID, action taken

- **send_payment_failure_email()** - Async email task
  - Notifies user of payment failure
  - Includes failure reason and retry link

- **auto_activate_pending_plans()** - Scheduled task (Celery Beat)
  - Activates queued plans when their start date arrives
  - Runs hourly

- **auto_downgrade_cancelled_subscriptions()** - Scheduled task
  - Downgrades subscriptions marked with cancel_at_period_end=True
  - Runs daily

### 4. **Signature Verification** (`subscription/tasks.py`)
- **verify_cashfree_signature()** - HMAC-SHA256 verification
  - Uses CASHFREE_WEBHOOK_SECRET
  - Base64-encoded signature comparison
  - Secure constant-time comparison

### 5. **Settings** (`cenvoras/settings.py`)
```python
CASHFREE_WEBHOOK_URL = 'https://api.cenvora.app/subscription/webhooks/cashfree/'
CASHFREE_WEBHOOK_SECRET = os.environ.get('CASHFREE_WEBHOOK_SECRET', '')
```

### 6. **URLs** (`subscription/urls.py`)
```python
path('webhooks/cashfree/', cashfree_webhook, name='cashfree_webhook'),
```

### 7. **Migration** (`subscription/migrations/0009_webhookevent.py`)
- Creates WebhookEvent model with indexes for performance

## Webhook Payload Format

Cashfree sends JSON with this structure:

```json
{
  "event": "PAYMENT_SUCCESS",
  "eventId": "evt_1234567890abcdef",
  "data": {
    "orderId": "order_abc123",
    "paymentId": "cf_payment_123456",
    "cf_payment_id": "cf_payment_123456",
    "error_message": null,
    "payment_status": "SUCCESS"
  }
}
```

## Processing Steps

### 1. Webhook Reception
- Endpoint receives POST from Cashfree
- Extracts `event_id`, `event_type`, `order_id`

### 2. Signature Verification
```python
signature = request.headers.get('x-cashfree-signature')
verify_cashfree_signature(payload_str, signature)
```

### 3. Idempotency Check
- Check if `event_id` already exists in WebhookEvent
- If exists and processed: return 200 OK (skip)
- If exists but not processed: continue processing
- If new: create WebhookEvent record

### 4. Queue Async Task
- Call `process_cashfree_webhook.delay()`
- Return 200 OK to Cashfree immediately
- Celery worker processes asynchronously

### 5. Payment Processing
- Get SubscriptionPayment by order_id
- Apply payment action (UPGRADE_NOW, RENEW, ACTIVATE)
- Update subscription state
- Queue email notification

### 6. Email Notification
- `send_async_email_notification()` queues email
- Email sent from `noreply@cenvora.app`
- Uses TRANSACTIONAL_EMAIL_API_KEY

## Subscription State Transitions

### UPGRADE_NOW (Mid-cycle upgrade)
- User on Pro, upgrades to Business before expiry
- Plan changes immediately
- Cycle end preserved
- Prorated charge applied

```
Pro (end: day 30) → Business (end: day 30)
```

### RENEW (Same plan renewal)
- User renews same plan before expiry
- Current period extended by 30 days

```
Pro (end: day 5) → Pro (end: day 35)
```

### ACTIVATE (New or expired subscription)
- User on free or expired
- Plan activated for 30 days

```
Free → Pro (new 30-day cycle)
Expired Pro → Pro (new 30-day cycle)
```

## Email Templates

### Payment Confirmation
```
Subject: Payment Confirmation - Cenvora

Hello [User],

Thank you for your payment! Your subscription has been successfully processed.

Plan: [Plan Name]
Amount: ₹[Amount]
Action: [Upgrade/Renewal/Activation]
Order ID: [Order ID]

Your plan is now active and you have full access to all features.

Best regards,
Cenvora Team
```

### Payment Failure
```
Subject: Payment Failed - Cenvora

Hello [User],

Unfortunately, your payment could not be processed.

Order ID: [Order ID]
Reason: [Failure Reason]

Please try again: https://cenvora.app/profile

Best regards,
Cenvora Team
```

## Celery Beat Scheduled Tasks

Add to your Celery Beat schedule (in settings.py or celery.py):

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'auto-activate-pending-plans': {
        'task': 'subscription.tasks.auto_activate_pending_plans',
        'schedule': crontab(minute=0),  # Every hour
    },
    'auto-downgrade-cancelled-subscriptions': {
        'task': 'subscription.tasks.auto_downgrade_cancelled_subscriptions',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
}
```

## Error Handling

### Webhook Signature Failures
- Return 401 Unauthorized
- Log error for investigation
- WebhookEvent NOT created (invalid request)

### Duplicate Webhooks
- WebhookEvent already exists
- Return 200 OK (idempotent)
- Skip reprocessing

### Processing Errors
- Celery auto-retries with exponential backoff (3 retries)
- Error logged in WebhookEvent.error_message
- Can be manually reviewed and retried

### Email Sending Failures
- Celery auto-retries (3 retries)
- Logged in NotificationLog
- User can check history in app

## Monitoring & Debugging

### Check Webhook Events
```python
from subscription.models import WebhookEvent

# Unprocessed events (likely errors)
WebhookEvent.objects.filter(processed=False)

# Recent events
WebhookEvent.objects.all()[:20]
```

### Check Payment Status
```python
from subscription.models import SubscriptionPayment

# Pending payments
SubscriptionPayment.objects.filter(status='pending')

# Failed payments
SubscriptionPayment.objects.filter(status='failed')
```

### Monitor Celery Tasks
```bash
# Start Celery worker (if not already running)
celery -A cenvoras worker -l info

# Start Celery Beat (for scheduled tasks)
celery -A cenvoras beat -l info

# Monitor in real-time
celery -A cenvoras events
```

## Migration & Deployment

### Step 1: Update Environment
Add these to your `.env`:
```bash
CASHFREE_WEBHOOK_URL=https://api.cenvora.app/subscription/webhooks/cashfree/
CASHFREE_WEBHOOK_SECRET=<get_from_cashfree_dashboard>
```

### Step 2: Run Migrations
```bash
python manage.py migrate subscription
```

### Step 3: Register Webhook in Cashfree
- Go to Cashfree Dashboard → Settings → Webhooks
- Add endpoint URL
- Copy webhook secret to env

### Step 4: Start Celery Workers
```bash
# Terminal 1: Celery Worker
celery -A cenvoras worker -l info

# Terminal 2: Celery Beat (for scheduled tasks)
celery -A cenvoras beat -l info
```

### Step 5: Test Webhook
```bash
# Using curl
curl -X POST https://api.cenvora.app/subscription/webhooks/cashfree/ \
  -H "Content-Type: application/json" \
  -H "x-cashfree-signature: test_signature" \
  -d '{
    "event": "PAYMENT_SUCCESS",
    "eventId": "evt_test_123",
    "data": {
      "orderId": "test_order_123",
      "paymentId": "cf_test_123"
    }
  }'
```

## Security Considerations

1. **Signature Verification** - All webhooks verified with HMAC-SHA256
2. **Idempotency** - Duplicate webhooks don't cause duplicate charges
3. **CSRF Exempt** - Webhook endpoint exempt from CSRF protection (required for Cashfree)
4. **No Authentication** - Webhook endpoint public (protected by signature)
5. **Async Processing** - Heavy operations don't block webhook response

## Future Enhancements

1. **Webhook Retry Policy** - Automatic retry of failed webhook processing
2. **Webhook Signature Rotation** - Support key rotation from Cashfree
3. **Webhook Audit Log** - Extended logging for compliance
4. **Payment Reconciliation** - Periodic sync with Cashfree to catch missed events
5. **Webhook Dashboard** - Admin panel to view and retry webhooks

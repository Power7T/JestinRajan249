# REST API Reference

Complete REST API endpoints for HostAI platform.

## Overview

**Base URL:** `https://api.yourdomain.com/api`

**Authentication:** Bearer token in `Authorization` header

**Response Format:** JSON

**Version:** v1

## Authentication

All API endpoints (except `/health` and `/webhooks/*`) require authentication.

### Get API Token

```bash
curl -X POST https://api.yourdomain.com/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "password"
  }'

# Response
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

### Use Token in Requests

```bash
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  https://api.yourdomain.com/api/properties
```

## Error Handling

All errors return JSON with `error` and `message` fields:

```json
{
  "error": "validation_error",
  "message": "Invalid property name",
  "details": {"field": "name", "error": "required"}
}
```

### Common Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 204 | No Content |
| 400 | Bad Request (validation error) |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (no permission) |
| 404 | Not Found |
| 409 | Conflict (resource already exists) |
| 429 | Too Many Requests (rate limited) |
| 500 | Server Error |

## Rate Limiting

API is rate-limited to **100 requests per minute** per token.

Response headers include:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1680000000
```

## Health Check

No authentication required.

### Check API Health

```bash
GET /health

# Response 200
{
  "status": "ok",
  "version": "1.0.0",
  "uptime_seconds": 3600
}
```

---

## Guest Contacts

Manage guest contact information.

### List Guest Contacts

```bash
GET /guest-contacts
  ?property_id=uuid
  &status=active
  &limit=50
  &offset=0

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "guest_name": "John Doe",
      "guest_phone": "+14155551234",
      "guest_email": "john@example.com",
      "room_identifier": "205",
      "property_name": "The Beachside",
      "check_in": "2026-03-31T14:00:00Z",
      "check_out": "2026-04-07T11:00:00Z",
      "status": "active",
      "created_at": "2026-03-31T10:00:00Z"
    }
  ],
  "total": 145,
  "limit": 50,
  "offset": 0
}
```

### Get Guest Contact

```bash
GET /guest-contacts/{id}

# Response 200
{
  "id": "uuid",
  "tenant_id": "uuid",
  "guest_name": "John Doe",
  "guest_phone": "+14155551234",
  "guest_email": "john@example.com",
  "room_identifier": "205",
  "property_name": "The Beachside",
  "check_in": "2026-03-31T14:00:00Z",
  "check_out": "2026-04-07T11:00:00Z",
  "status": "active",
  "created_at": "2026-03-31T10:00:00Z"
}
```

### Create Guest Contact

```bash
POST /guest-contacts

{
  "guest_name": "John Doe",
  "guest_phone": "+14155551234",
  "guest_email": "john@example.com",
  "room_identifier": "205",
  "property_name": "The Beachside",
  "check_in": "2026-03-31T14:00:00Z",
  "check_out": "2026-04-07T11:00:00Z",
  "status": "active"
}

# Response 201
{
  "id": "uuid",
  "tenant_id": "uuid",
  ...
}
```

### Update Guest Contact

```bash
PATCH /guest-contacts/{id}

{
  "room_identifier": "206",
  "status": "checkout"
}

# Response 200
{
  "id": "uuid",
  ...
}
```

### Delete Guest Contact

```bash
DELETE /guest-contacts/{id}

# Response 204 (no content)
```

---

## Reservations

Manage property reservations.

### List Reservations

```bash
GET /reservations
  ?property_id=uuid
  &status=confirmed
  ?checkin_from=2026-03-01
  ?checkin_to=2026-04-01
  &limit=50

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "guest_name": "Jane Smith",
      "guest_phone": "+14155555678",
      "guest_email": "jane@example.com",
      "confirmation_code": "AIRBNB123456",
      "listing_name": "Ocean View Villa",
      "unit_identifier": "A1",
      "checkin": "2026-04-01",
      "checkout": "2026-04-08",
      "nights": 7,
      "source": "airbnb",
      "status": "confirmed",
      "special_requests": "High chair needed",
      "created_at": "2026-03-25T10:00:00Z"
    }
  ],
  "total": 342,
  "limit": 50
}
```

### Get Reservation

```bash
GET /reservations/{id}

# Response 200
{ ... }
```

### Create Reservation

```bash
POST /reservations

{
  "guest_name": "Jane Smith",
  "guest_phone": "+14155555678",
  "guest_email": "jane@example.com",
  "confirmation_code": "AIRBNB123456",
  "listing_name": "Ocean View Villa",
  "unit_identifier": "A1",
  "checkin": "2026-04-01",
  "checkout": "2026-04-08",
  "nights": 7,
  "source": "airbnb",
  "status": "confirmed",
  "special_requests": "High chair needed"
}

# Response 201
{ ... }
```

### Bulk Import Reservations

```bash
POST /reservations/bulk-import

{
  "format": "csv",
  "data": "guest_name,checkin,checkout,...\nJohn Doe,2026-04-01,2026-04-08,..."
}

# Response 200
{
  "imported": 42,
  "errors": [],
  "warnings": ["Row 3: Missing guest email"]
}
```

### Update Reservation

```bash
PATCH /reservations/{id}

{
  "unit_identifier": "B2",
  "special_requests": "Updated request"
}

# Response 200
{ ... }
```

### Delete Reservation

```bash
DELETE /reservations/{id}

# Response 204
```

---

## Voice Calls

Manage voice call records and analytics.

### List Voice Calls

```bash
GET /voice-calls
  ?property_id=uuid
  ?date_from=2026-03-01
  ?date_to=2026-03-31
  ?sentiment=positive
  &limit=50

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "guest_name": "John Doe",
      "guest_phone": "+14155551234",
      "twilio_call_id": "CA...",
      "call_type": "incoming",
      "status": "completed",
      "duration_seconds": 145,
      "guest_messages": ["What is the WiFi password?"],
      "ai_responses": ["The WiFi network is 'BeachsideGuest'..."],
      "full_transcript": "...",
      "recording_url": "https://r2.example.com/call-123.wav",
      "sentiment": "positive",
      "created_at": "2026-03-31T14:22:00Z",
      "started_at": "2026-03-31T14:22:10Z",
      "ended_at": "2026-03-31T14:24:35Z"
    }
  ],
  "total": 512,
  "limit": 50
}
```

### Get Voice Call

```bash
GET /voice-calls/{id}

# Response 200
{ ... full call details ... }
```

### Voice Call Metrics

```bash
GET /voice-calls/metrics
  ?property_id=uuid
  ?period=month
  ?date_from=2026-03-01
  ?date_to=2026-03-31

# Response 200
{
  "total_calls": 512,
  "total_minutes": 1240,
  "average_duration_seconds": 145,
  "completion_rate": 0.94,
  "sentiment_distribution": {
    "positive": 0.65,
    "neutral": 0.25,
    "negative": 0.10
  },
  "cost_total": 182.50,
  "cost_per_call": 0.36,
  "knowledge_gaps": 23
}
```

---

## Messages

Manage multi-channel messages (Email, WhatsApp, SMS).

### List Messages

```bash
GET /messages
  ?guest_id=uuid
  ?channel=email
  ?status=sent
  &limit=50

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "guest_id": "uuid",
      "channel": "email",
      "direction": "inbound",
      "subject": "WiFi password?",
      "body": "Hi, what is the WiFi password for the property?",
      "sender": "john@example.com",
      "recipient": "host@example.com",
      "status": "received",
      "created_at": "2026-03-31T14:20:00Z"
    }
  ],
  "total": 1240,
  "limit": 50
}
```

### Get Message

```bash
GET /messages/{id}

# Response 200
{ ... }
```

### Send Message

```bash
POST /messages

{
  "guest_id": "uuid",
  "channel": "email",
  "subject": "WiFi Password for Your Stay",
  "body": "Hi John, here's your WiFi password: ...",
  "schedule_at": "2026-03-31T15:00:00Z"
}

# Response 201
{
  "id": "uuid",
  "status": "scheduled",
  ...
}
```

### Get Draft Reply

```bash
GET /messages/{id}/draft-reply

# Response 200
{
  "draft": "Thanks for reaching out! Here's the WiFi password...",
  "confidence": 0.92,
  "tone": "friendly"
}
```

---

## Properties

Manage property information.

### List Properties

```bash
GET /properties
  &limit=50

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "tenant_id": "uuid",
      "name": "The Beachside",
      "type": "villa",
      "address": "123 Ocean Ave",
      "city": "Santa Monica",
      "state": "CA",
      "zip": "90401",
      "country": "USA",
      "amenities": ["WiFi", "Hot Tub", "Pool", "Parking"],
      "house_rules": ["Quiet after 10 PM", "No smoking"],
      "checkin_time": "15:00",
      "checkout_time": "11:00",
      "wifi_ssid": "BeachsideGuest",
      "ical_feed": "https://...",
      "created_at": "2026-01-15T10:00:00Z"
    }
  ],
  "total": 8,
  "limit": 50
}
```

### Get Property

```bash
GET /properties/{id}

# Response 200
{ ... }
```

### Create Property

```bash
POST /properties

{
  "name": "The Beachside",
  "type": "villa",
  "address": "123 Ocean Ave",
  "city": "Santa Monica",
  "state": "CA",
  "zip": "90401",
  "country": "USA",
  "amenities": ["WiFi", "Hot Tub", "Pool"],
  "house_rules": ["Quiet after 10 PM"],
  "checkin_time": "15:00",
  "checkout_time": "11:00",
  "wifi_ssid": "BeachsideGuest"
}

# Response 201
{ ... }
```

### Update Property

```bash
PATCH /properties/{id}

{
  "name": "The Beachside Villa",
  "amenities": ["WiFi", "Hot Tub", "Pool", "Gym"]
}

# Response 200
{ ... }
```

### Delete Property

```bash
DELETE /properties/{id}

# Response 204
```

---

## Billing & Subscriptions

Manage subscriptions and billing.

### Get Subscription

```bash
GET /subscription

# Response 200
{
  "id": "uuid",
  "plan": "Growth",
  "status": "active",
  "current_period_start": "2026-03-31",
  "current_period_end": "2026-04-30",
  "price_per_month": 20,
  "units": 8,
  "voice_addon": {
    "tier": "Standard",
    "price_per_month": 79,
    "minutes_included": 300,
    "overage_rate": 0.049
  },
  "next_billing_date": "2026-04-30",
  "auto_renew": true
}
```

### Update Subscription

```bash
PATCH /subscription

{
  "plan": "Pro",
  "units": 25,
  "voice_addon_tier": "Professional"
}

# Response 200
{
  "id": "uuid",
  "plan": "Pro",
  ...
}
```

### Cancel Subscription

```bash
POST /subscription/cancel

{
  "effective_date": "2026-04-30",
  "reason": "Switching providers"
}

# Response 200
{
  "status": "pending_cancellation",
  "cancellation_date": "2026-04-30"
}
```

### Get Billing History

```bash
GET /billing/invoices
  ?limit=12

# Response 200
{
  "data": [
    {
      "id": "uuid",
      "invoice_number": "INV-2026-0031",
      "date": "2026-03-31",
      "amount": 99.00,
      "status": "paid",
      "pdf_url": "https://r2.example.com/invoice-123.pdf",
      "items": [
        {"description": "Growth Plan (8 units)", "amount": 20.00},
        {"description": "Standard Voice Add-on", "amount": 79.00}
      ]
    }
  ],
  "total": 42
}
```

### Get Invoice

```bash
GET /billing/invoices/{id}

# Response 200
{ ... invoice details ... }
```

---

## Admin Endpoints

**Requires admin role**

### Get Tenant

```bash
GET /admin/tenants/{id}

# Response 200
{
  "id": "uuid",
  "name": "John's Hosting Business",
  "domain": "johns-hosting.com",
  "plan": "Pro",
  "status": "active",
  "created_at": "2026-01-15T10:00:00Z",
  "subscription": { ... }
}
```

### List Tenants

```bash
GET /admin/tenants
  ?status=active
  &limit=50

# Response 200
{
  "data": [ ... ],
  "total": 234,
  "limit": 50
}
```

### Update Tenant

```bash
PATCH /admin/tenants/{id}

{
  "plan": "Growth",
  "status": "suspended"
}

# Response 200
{ ... }
```

### Get Admin Metrics

```bash
GET /admin/metrics
  ?period=month

# Response 200
{
  "mrr": 12450.00,
  "arr": 149400.00,
  "churn_rate": 0.02,
  "new_customers": 12,
  "active_customers": 234,
  "total_calls": 5120,
  "voice_revenue": 3240.00
}
```

### Update Voice Pricing

```bash
PATCH /admin/pricing/voice

{
  "tiers": [
    {
      "name": "Light",
      "price": 39.00,
      "minutes": 100,
      "overage_rate": 0.049
    },
    {
      "name": "Standard",
      "price": 79.00,
      "minutes": 300,
      "overage_rate": 0.049
    }
  ]
}

# Response 200
{
  "updated": true,
  "margins": [
    {"tier": "Light", "margin": 0.88},
    {"tier": "Standard", "margin": 0.58}
  ]
}
```

---

## Webhooks

### Supported Webhook Events

- `voice.call.completed` - Voice call finished
- `message.received` - New inbound message
- `reservation.created` - New reservation
- `reservation.updated` - Reservation changed
- `subscription.changed` - Plan changed
- `knowledge_gap.created` - New unanswered question

### Register Webhook

```bash
POST /webhooks

{
  "url": "https://your-app.com/webhooks/hostai",
  "events": ["voice.call.completed", "message.received"],
  "secret": "webhook-secret-for-verification"
}

# Response 201
{
  "id": "uuid",
  "url": "https://your-app.com/webhooks/hostai",
  "events": ["voice.call.completed", "message.received"],
  "active": true
}
```

### Webhook Payload Example

```json
{
  "event": "voice.call.completed",
  "timestamp": "2026-03-31T14:24:35Z",
  "data": {
    "call_id": "uuid",
    "guest_name": "John Doe",
    "duration_seconds": 145,
    "sentiment": "positive",
    "has_knowledge_gap": false
  },
  "signature": "sha256=..."
}
```

### Verify Webhook Signature

```python
import hmac
import hashlib

secret = "webhook-secret"
body = request.body
signature = request.headers.get("X-Webhook-Signature")

expected = "sha256=" + hmac.new(
    secret.encode(),
    body,
    hashlib.sha256
).hexdigest()

assert hmac.compare_digest(signature, expected)
```

---

## SDK Examples

### Python

```python
import requests

class HostAIClient:
    def __init__(self, base_url, token):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}

    def list_voice_calls(self, limit=50):
        response = requests.get(
            f"{self.base_url}/voice-calls",
            headers=self.headers,
            params={"limit": limit}
        )
        return response.json()

    def send_message(self, guest_id, channel, subject, body):
        response = requests.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json={
                "guest_id": guest_id,
                "channel": channel,
                "subject": subject,
                "body": body
            }
        )
        return response.json()

# Usage
client = HostAIClient("https://api.yourdomain.com/api", "token_here")
calls = client.list_voice_calls()
```

---

See **[README.md](../README.md)** for API documentation link.

See **[DEVELOPMENT.md](DEVELOPMENT.md)** for running API locally.

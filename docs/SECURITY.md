# Security & Compliance

Security practices, compliance features, and guidelines for HostAI.

## Overview

HostAI is built with security and compliance as core requirements:

- **CSRF Protection** — All state changes verified with tokens
- **Webhook Signatures** — Twilio, Meta, Mailgun, Stripe signatures validated
- **Rate Limiting** — Per-tenant and per-IP limits
- **Data Encryption** — Sensitive fields encrypted at rest
- **Audit Logging** — Complete activity trail for compliance
- **2FA Support** — Optional two-factor authentication for admin users
- **GDPR/CCPA Ready** — Data export, deletion, consent tracking

## Authentication & Authorization

### Password Security

**Requirements:**
- Minimum 12 characters
- Must contain uppercase, lowercase, number, symbol
- Cannot be common passwords
- Cannot reuse last 5 passwords

**Password hashing:**
- bcrypt with 12 rounds
- Never stored in plain text
- Cannot be retrieved, only reset

### Session Management

**JWT Tokens:**
- Signed with SECRET_KEY
- Expire after 24 hours (configurable)
- Refreshable with refresh token
- Stored as HTTP-only cookies (XSS protection)

**CSRF Protection:**
- CSRF token required for POST/PUT/DELETE/PATCH
- Token stored in session
- Token validated on each state-changing request
- Token regenerated after login

### Multi-Factor Authentication (2FA)

Optional 2FA for admin users:

**Setup:**
1. Admin user enables 2FA in settings
2. Scan QR code with authenticator app
3. Enter 6-digit code to verify
4. Backup codes generated for recovery

**Login with 2FA:**
1. Enter email and password
2. Authenticator app shows 6-digit code
3. Enter code
4. Session established

**Recovery codes:**
- Generated when 2FA enabled
- 10 one-time use codes
- Stored securely, can't be retrieved
- Regenerate anytime in settings

### Role-Based Access Control (RBAC)

**Tenant roles:**
- **Admin** — Full access to tenant account
- **User** — Can manage properties and messages
- **Viewer** — Read-only access

**Platform admin roles:**
- **Admin** — Everything (tenants, pricing, users)
- **Analyst** — View metrics and reports (read-only)
- **Support** — Manage customer accounts, issue refunds
- **Billing** — Invoice and pricing management

Each role has specific permissions checked on every request.

## Data Security

### Encryption at Rest

**Sensitive fields encrypted:**
- Guest phone numbers
- Guest email addresses
- API keys and tokens
- Stripe customer tokens
- Twilio account details

**Encryption method:**
- AES-256-GCM
- Key rotation supported
- Encrypted in database (PostgreSQL)

### Encryption in Transit

- **TLS 1.2+** on all connections (HTTPS)
- **WSS** (WebSocket Secure) for real-time updates
- **HSTS** headers with 1-year max-age
- Certificate pinning on mobile apps (future)

### Database Security

**Connection:**
- PostgreSQL user has minimal required permissions
- Connection over private network (Railway)
- Connection pooling with PgBouncer
- SSL required for connections

**Backups:**
- Automated daily backups
- Encrypted at rest
- Retained for 30 days
- Tested regularly for recovery

**Access control:**
- Only app can read/write database
- Admin SSH access logged
- Read replicas for analytics only

### API Key Security

**Never exposed:**
- API keys never logged
- Never sent in URLs (always in request body)
- Rotatable from settings
- Revokable if compromised

**Storage:**
- Encrypted in database
- Accessible only via admin panel
- Generation logged in audit trail

### Password Reset

**Security:**
- Reset token expires after 15 minutes
- Token is single-use
- Token invalidated after use
- User must verify email to reset

## Input Validation & Output Encoding

### SQL Injection Prevention

- Parameterized queries everywhere (SQLAlchemy ORM)
- No raw SQL queries
- Input validation on all user data

### XSS (Cross-Site Scripting) Prevention

- Output HTML-encoded
- React components use .textContent not innerHTML
- Content Security Policy headers enabled
- No inline JavaScript

### CSRF (Cross-Site Request Forgery) Prevention

- CSRF token required for state-changing requests
- Token in hidden form field or X-CSRF-Token header
- Token verified before processing request
- SameSite cookie attribute set to Strict

### Data Validation

```python
# Example: Email validation
from pydantic import EmailStr

class GuestContactCreate(BaseModel):
    guest_email: EmailStr
    guest_phone: str = Field(..., regex=r'^\+?1?\d{9,15}$')
    room_identifier: str = Field(..., min_length=1, max_length=50)
```

All inputs validated with Pydantic before use.

## Webhook Security

### Signature Validation

All webhooks validated before processing.

**Twilio webhooks:**
```python
from twilio.request_validator import RequestValidator

validator = RequestValidator(TWILIO_AUTH_TOKEN)
if not validator.validate(url, body, signature):
    return 403, "Invalid signature"
```

**Stripe webhooks:**
```python
import stripe

event = stripe.Webhook.construct_event(
    payload, signature, endpoint_secret
)
```

**Meta (WhatsApp) webhooks:**
```python
import hashlib
import hmac

def verify_meta_signature(request_body, signature, secret):
    expected = hmac.new(
        secret.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

### Webhook Rate Limiting

- Per-webhook rate limits
- Prevents replay attacks
- Failed deliveries retried with exponential backoff

## Rate Limiting & DDoS Protection

### API Rate Limiting

**Per API key:**
- 100 requests/minute (configurable)
- Returns 429 when exceeded
- Rate limit headers included

**Per IP:**
- 1000 requests/minute
- Blocks for 1 hour if exceeded

**Per tenant:**
- Soft limit (warnings) at 80 requests/minute
- Hard limit (blocks) at 100 requests/minute

### DDoS Protection

Configure in production:

**Using Cloudflare:**
1. Add domain to Cloudflare
2. Enable DDoS protection
3. Set rate limiting rules
4. Cloudflare blocks attacks before reaching app

**Example rules:**
```
Block requests > 100/minute from same IP
Block requests with invalid User-Agent
Block requests with rate > 1000/minute
```

## Audit Logging

### What's Logged

Admin actions:
- User login/logout
- Plan changes
- Pricing updates
- Tenant suspension
- Invoice generation
- Data exports

API actions:
- All API calls with user/token
- Request method and path
- Response status code
- Errors and stack traces
- IP address

Database:
- All DDL changes (schema)
- Sensitive data access (encryption key changes)
- Backup operations

### Log Retention

- Real-time logs: 7 days
- Archived logs: 90 days
- Audit logs (compliance): 1 year
- Logs encrypted at rest

### Log Queries

```bash
# Find all voice call accesses
grep "voice_calls" /var/log/hostai/api.log

# Find all pricing changes
grep "pricing" /var/log/hostai/admin.log | grep "PATCH"

# Find failed login attempts
grep "login.*failed" /var/log/hostai/auth.log
```

## GDPR Compliance

### Data Subject Rights

**Right to Access:**
- `/api/export` endpoint exports all customer data
- Available in Admin → Settings → Data Export
- Format: JSON, CSV, or SQL

**Right to Erasure (Right to be Forgotten):**
- Tenant can request data deletion
- Guest data deletable from guest database
- Recordings auto-delete after 90 days (configurable)
- Historical billing data retained as required

**Right to Data Portability:**
- Export all data in standard formats
- Import to competitor's system

**Right to Rectification:**
- Admin panel allows editing all customer data
- Audit trail shows all corrections

### Processing Documentation

**Data Processing Agreement (DPA):**
- Available at `https://yourdomain.com/legal/dpa`
- Specifies what data is processed
- How long it's retained
- Who has access

**GDPR Clauses:**
- Standard Contractual Clauses for EU-US data transfers
- Data Processing Addendum signed with customers
- Regular data protection assessments

### Consent Management

**Recording Consent:**
- Recorded in `VoiceCall.recording_consent` field
- Tracked by guest and call
- Audit trail of when consent given/withdrawn
- No recording if consent not given

## CCPA Compliance

### California Privacy Rights

**Right to Know:**
- Customers can request all data about them
- Export available within 45 days

**Right to Delete:**
- Request deletion of personal information
- Processed within 45 days
- Exception: business records kept for compliance

**Right to Opt-Out:**
- Opt-out of sale of personal info
- Disable marketing communications

### CCPA Disclosures

**Privacy Policy includes:**
- What categories of data collected
- Why data collected
- How data used
- Retention periods
- Customer rights

Link in footer: `/privacy`

## Payment Security

### PCI DSS Compliance

**What we do:**
- Never store credit card numbers
- Use Stripe for payment processing
- Stripe handles PCI compliance
- Tokens used instead of card numbers

**What customers should do:**
- Never type card numbers in any field except Stripe
- Use Stripe's hosted payment form
- Don't share payment info via email/chat

### Secure Payment Flow

1. Customer enters card info in Stripe's hosted form (secure)
2. Stripe returns token (e.g., `tok_visa`)
3. App sends token to Stripe (not card number)
4. Stripe charges and confirms
5. Card number never touches your servers

## Infrastructure Security

### Network Security

**Production network (Railway):**
- Private PostgreSQL (not exposed to internet)
- Private Redis (not exposed)
- Only web service has public IP
- Firewall rules block unnecessary traffic

**Firewall:**
- Allow: HTTPS (443) and HTTP (80, redirects to HTTPS)
- Allow: SSH (22, restricted to admin IPs)
- Deny: Everything else

### Server Security

**Security updates:**
- Automatic security patches for OS
- Python security updates: manual with testing
- Library updates: weekly with automated tests
- Critical CVE fixes: immediate

**SSH Access:**
- Key-based authentication only (no passwords)
- Root login disabled
- SSH agent forwarding disabled
- 2FA required for admin access

### Container Security

**Docker image scanning:**
- Scan base image for vulnerabilities
- Scan dependencies with Snyk or similar
- Update base image monthly

**Image signing:**
- Sign production images
- Verify signature before deployment

## Dependency Security

### Dependency Management

**Tools used:**
- `pip-audit` — Detect vulnerable Python packages
- `safety` — Check Python dependencies
- `Snyk` — Scan container images

**Process:**
1. New dependencies reviewed for security
2. Weekly scans for vulnerabilities
3. Critical vulnerabilities patched immediately
4. Monthly update cycle for non-critical

**Example:**
```bash
# Scan requirements.txt for vulnerabilities
pip-audit --desc

# Fix vulnerable package
pip install --upgrade package-name
```

## Error Handling & Logging

### Safe Error Messages

**Don't expose:**
- Database structure or queries
- Full stack traces to users
- File paths or internal IPs
- Authentication details

**Example of safe error:**
```python
# ❌ BAD - Exposes database
raise Exception(f"User '{email}' not found in table 'users'")

# ✅ GOOD - Generic message
raise ValueError("User not found")
```

### Logging Sensitive Data

**Never log:**
- Passwords or tokens
- Credit card numbers
- Encryption keys
- Personal identification numbers

**Safe logging:**
```python
# ✅ Good - Hash or redact
logger.info(f"Payment from {email_hash}... succeeded")
logger.info(f"Card ending in ...{card_last_4} charged")
```

## Security Checklist

### Development

- [ ] Use environment variables for secrets (never hardcode)
- [ ] Validate all user input
- [ ] Encode output for context (HTML, SQL, etc.)
- [ ] Use parameterized queries (ORM)
- [ ] Never trust client input
- [ ] Log security events
- [ ] Test authentication and authorization
- [ ] Scan dependencies for vulnerabilities

### Deployment

- [ ] Enable HTTPS/TLS
- [ ] Set CSRF protection enabled
- [ ] Configure CORS properly
- [ ] Rate limiting enabled
- [ ] Security headers set (HSTS, CSP)
- [ ] Secrets in environment variables
- [ ] Database backups working
- [ ] Logs being collected
- [ ] Monitoring and alerts configured
- [ ] Firewall rules configured

### Operations

- [ ] Regular security updates applied
- [ ] Logs reviewed regularly
- [ ] Failed login attempts monitored
- [ ] Unusual API usage detected
- [ ] Webhook deliveries successful
- [ ] SSL/TLS certificates renewed before expiry
- [ ] Backup restores tested
- [ ] Admin access audited

## Incident Response

### Report a Security Issue

**Do not file public issues for security problems.**

Email security-report@yourdomain.com with:
- Description of vulnerability
- Steps to reproduce
- Potential impact
- Your contact information

We respond within 24 hours and credit responsible disclosers.

### Security Incident Procedure

1. **Detect** — Monitor for unusual activity
2. **Contain** — Isolate affected systems if necessary
3. **Investigate** — Determine scope and cause
4. **Remediate** — Fix vulnerability and apply patch
5. **Notify** — Inform affected customers (if data exposed)
6. **Review** — Post-incident analysis and improvements

### Downtime During Incident

If security incident requires downtime:
- Customer portal shows maintenance page
- Email notification sent to admin
- Incident report published after resolution

## Security Resources

### External Resources

- **OWASP Top 10:** https://owasp.org/www-project-top-ten/
- **PCI DSS:** https://www.pcisecuritystandards.org/
- **GDPR Compliance:** https://gdpr-info.eu/
- **CCPA Compliance:** https://cppa.ca.gov/
- **CWE Top 25:** https://cwe.mitre.org/top25/

### Staying Updated

- Subscribe to security mailing lists
- Follow @HostAI on Twitter for security updates
- Monitor GitHub for security advisories
- Regular security training for team

## Support

For security questions or concerns:
- Email: security@yourdomain.com
- Documentation: See `/docs` directory
- Issues: GitHub Issues (public questions only)

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for production security setup.

See **[ADMIN.md](ADMIN.md)** for admin security features.

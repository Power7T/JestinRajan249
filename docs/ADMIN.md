# Admin Panel Documentation

Complete guide to the HostAI admin panel for SaaS management.

## Overview

The admin panel is the control center for managing:
- Multi-tenant SaaS infrastructure
- Customer billing and subscriptions
- Voice AI pricing and margins
- Real-time platform metrics
- User management and permissions

**Access:** `https://app.yourdomain.com/admin`

## Dashboard

The main admin dashboard shows real-time metrics.

### Key Metrics

**Revenue:**
- **MRR** (Monthly Recurring Revenue) — Total subscription revenue
- **ARR** (Annual Recurring Revenue) — MRR × 12
- **Total MRR breakdown** — By plan (Starter, Growth, Pro) and voice add-ons

**Customers:**
- **Total active customers** — Currently subscribed
- **New customers this month** — Acquisition rate
- **Churn rate** — % of customers leaving per month
- **Net retention** — MRR growth rate

**Voice AI:**
- **Total calls this month** — Incoming + outbound
- **Total minutes** — Sum of all call durations
- **Average call duration** — Mean length in seconds
- **Cost of goods** — Total API costs (Twilio, Deepgram, OpenAI, ElevenLabs)
- **Voice revenue** — Sum of voice add-on subscriptions
- **Voice margin** — (Voice Revenue - COGS) / Voice Revenue

**Infrastructure:**
- **API uptime** — % of time service is available
- **Database health** — Query latency, connection pool usage
- **Error rate** — % of requests returning errors

### Date Range Selection

All metrics support custom date ranges:
- Last 7 days
- Last 30 days
- Last 90 days
- Last year
- Custom date range

## Tenant Management

Manage all customer accounts.

### List Tenants

**Navigation:** Admin → Tenants

View all customers with:
- Name and domain
- Current plan (Starter, Growth, Pro)
- Status (active, suspended, cancelled)
- MRR contribution
- Created date
- Last activity

**Filters:**
- Status: active, suspended, trial, cancelled
- Plan: Starter, Growth, Pro
- Search by name or domain

**Actions:**
- View tenant details
- Edit tenant information
- Change plan
- Suspend/reactivate account
- View all properties and guests
- Export data

### View Tenant Details

**Navigation:** Admin → Tenants → {tenant name}

See:
- Account information (name, domain, email)
- Current subscription (plan, status, renewal date)
- Billing contact information
- Properties (count and list)
- Voice AI usage (calls, minutes, costs)
- Messages sent (by channel)
- Total guests in database
- Admin notes

### Edit Tenant

**Navigation:** Admin → Tenants → {tenant} → Edit

Update:
- Business name
- Domain
- Billing contact email
- Admin notes
- Custom properties

### Change Tenant Plan

**Navigation:** Admin → Tenants → {tenant} → Change Plan

1. Select new plan (Starter, Growth, Pro)
2. Confirm unit count
3. Select voice add-on tier (Light, Standard, Professional, Unlimited, None)
4. Choose effective date:
   - Immediate (prorated)
   - At renewal date (next billing cycle)
5. Send notification email to customer (optional)
6. Apply

**System calculates:**
- Prorated credit for remaining month
- New invoice amount
- Billing adjustment details

### Suspend Tenant

Temporarily suspend a customer account:

**Navigation:** Admin → Tenants → {tenant} → Suspend

Reasons (optional):
- Overdue payment
- Terms of service violation
- Requested by customer
- Other (specify)

When suspended:
- All API access blocked
- Voice AI disabled
- Email/SMS notifications fail
- Customer portal shows suspension notice

### Manage Permissions

**Navigation:** Admin → Tenants → {tenant} → Users

View all users with admin access:
- Email
- Role (Admin, User, Viewer)
- Last login
- 2FA status

Create new admin user:
1. Click "Add User"
2. Enter email address
3. Select role (Admin = full access, User = limited, Viewer = read-only)
4. Send invitation email
5. User clicks link to set password

Delete user:
- Click "Remove" next to user
- Confirm action

## Billing Management

Manage subscriptions, invoices, and payments.

### View Customer Subscription

**Navigation:** Admin → Tenants → {tenant} → Subscription

Shows:
- Current plan
- Status (active, trial, past_due, cancelled)
- Current period dates
- Price per month
- Unit count
- Voice add-on tier and pricing
- Next billing date
- Auto-renewal status

### Issue Refund

**Navigation:** Admin → Tenants → {tenant} → Invoices → {invoice} → Refund

1. Enter refund amount (full or partial)
2. Select refund reason
3. Confirm
4. Stripe processes refund
5. Invoice shows "Refunded" status

### Send Invoice Reminder

**Navigation:** Admin → Tenants → {tenant} → Invoices → {invoice} → Resend

Sends email reminder to customer with:
- Invoice PDF
- Payment link
- Due date

### Track Failed Payments

**Navigation:** Admin → Billing → Failed Payments

Shows customers with:
- Overdue invoices
- Failed payment attempts
- Days overdue
- Total amount due

**Actions:**
- Send payment reminder
- Update payment method
- Manually retry charge
- Suspend account

## Voice AI Pricing

Dynamically adjust voice pricing tiers in real-time.

### View Current Pricing

**Navigation:** Admin → Pricing → Voice AI

Shows all 4 tiers:

| Tier | Monthly | Minutes | Overage | Current Margin |
|------|---------|---------|---------|-----------------|
| Light | $39 | 100 | $0.049/min | 88% |
| Standard | $79 | 300 | $0.049/min | 58% |
| Professional | $129 | 750 | $0.049/min | 36% |
| Unlimited | $199 | Unlimited | Free | Variable |

### Update Tier Pricing

1. Click "Edit" next to tier
2. Update fields:
   - **Monthly price** — What customers pay
   - **Included minutes** — How many free minutes
   - **Overage rate** — Cost per extra minute
3. System automatically calculates new margin
4. **Margin shows in real-time:**
   - **Green** (✓) — Margin is above 70% target
   - **Yellow** (⚠) — Margin between 50-70%
   - **Red** (✗) — Margin below 50% (warning)
5. Click "Update" to apply
6. New pricing applies to:
   - New customers immediately
   - Existing customers at next renewal

### View Margin Analysis

Click "Margin Analysis" to see:
- Cost basis per tier (API costs)
- Revenue per tier (average customer)
- Gross margin per tier
- Comparison vs. target (70%)
- Recommendations for pricing

**Example:**
```
Light Tier:
- API cost per 100 calls: $4.40
- Revenue: $39/month
- Margin: 88% ✓ (Above target)
- Recommendation: Good for price-sensitive customers

Professional Tier:
- API cost per 750 calls: $82.50
- Revenue: $129/month
- Margin: 36% ⚠ (Below target - consider raising price)
- Recommendation: Increase to $149/month for 50% margin
```

### Set Surge Pricing

During high-demand periods, temporarily increase prices:

**Navigation:** Admin → Pricing → Surge Pricing

1. Enable surge pricing
2. Set multiplier (e.g., 1.5x for 50% increase)
3. Set duration (hours or until manual disable)
4. Confirm

When active:
- New customer signups see increased prices
- Existing customers unaffected
- Dashboard shows "Surge Active" badge
- Auto-disables after duration expires

### Pricing History

View all pricing changes:

**Navigation:** Admin → Pricing → History

Shows:
- Date/time of change
- Admin who made change
- Old pricing
- New pricing
- Affected customers
- Reason for change (optional)

## Analytics & Reports

Deep insights into platform usage and metrics.

### Voice Analytics

**Navigation:** Admin → Analytics → Voice

**Period selection:** Day, Week, Month, Year, Custom

Metrics:
- **Call volume** — Daily/weekly breakdown
- **Average call duration** — Minutes and seconds
- **Call completion rate** — % of calls answered
- **Cost per call** — Weighted by minutes used
- **Revenue per call** — Blended across tiers
- **Margin per call** — Revenue minus API cost
- **Sentiment distribution** — % positive/neutral/negative
- **Knowledge gaps** — Unanswered questions escalated

**Filters:**
- By tenant/customer
- By property
- By source (incoming calls, outbound)

**Export:** CSV or PDF report

### Customer Analytics

**Navigation:** Admin → Analytics → Customers

Metrics:
- **New customers this period** — Acquisition
- **Churn rate** — % leaving per month
- **Retention rate** — % staying
- **Lifetime value** — Average revenue per customer
- **Customer acquisition cost** — Marketing spend / new customers
- **LTV:CAC ratio** — How many months to break even
- **Average customer lifespan** — Months before churn

**Cohort analysis:**
- Segment customers by signup date
- Track retention by cohort
- Identify trends in customer longevity

### Revenue Analytics

**Navigation:** Admin → Analytics → Revenue

Breakdown by:
- Plan type (Starter, Growth, Pro)
- Voice add-on tier
- Geographic region
- Customer segment

Metrics:
- MRR (monthly recurring revenue)
- ARR (annual recurring revenue)
- ARPU (average revenue per user) by segment
- Revenue growth rate
- Revenue concentration (% from top 10% of customers)

**Forecasting:**
- Estimated MRR at different churn rates
- Revenue impact of price changes
- Customer acquisition needed to hit targets

### Message Analytics

**Navigation:** Admin → Analytics → Messages

By channel:
- **Email** — Sent, delivered, opened, clicked
- **WhatsApp** — Messages sent, read rate
- **SMS** — Delivery rate, cost

Metrics:
- Response time (host to guest)
- Message volume trends
- Common topics/keywords
- Escalation rate to support

## User Management

Manage admin users and their permissions.

### Admin Users

**Navigation:** Admin → Settings → Users

List of all admin accounts:
- Email
- Role (Admin, Analyst, Support, Billing)
- Last login
- 2FA status
- Invite status (pending/accepted)

**Roles:**

| Role | Permissions |
|------|------------|
| Admin | Everything (create tenants, change pricing, manage users) |
| Analyst | View metrics and reports (read-only analytics) |
| Support | View tenant data, suspend accounts, issue refunds |
| Billing | View billing and pricing, manage invoices |

### Create Admin User

1. Click "Add User"
2. Enter email address
3. Select role
4. Click "Send Invitation"
5. User receives email with link
6. User creates password
7. User signs in with email + password + 2FA

### Reset Admin Password

1. Go to Users
2. Click "Reset" next to user
3. System sends password reset email
4. User clicks link and sets new password

### Enable 2FA for Admin Account

**Navigation:** Admin → Settings → Security → 2FA

1. Click "Enable 2FA"
2. Scan QR code with authenticator app (Google Authenticator, Authy, etc.)
3. Enter 6-digit code from app
4. Confirm
5. Save backup codes in secure location

Next login requires authenticator app code.

## Security & Compliance

### Audit Logs

**Navigation:** Admin → Settings → Audit Logs

View all admin actions:
- Who made the change
- What changed
- When (date/time)
- Old value
- New value

Examples:
- Tenant plan changed from Growth to Pro
- Voice pricing updated (Light tier)
- Invoice manually issued
- User suspended

Filter by:
- Action type
- Date range
- Admin user
- Tenant

**Export** audit logs for compliance reports.

### Data Export

**Navigation:** Admin → Settings → Data Export

Export all platform data:
- All tenants (JSON, CSV, SQL dump)
- All properties and guests
- All reservations
- All voice calls
- All messages
- Billing data

**Uses:**
- GDPR data requests
- Backup and disaster recovery
- Data analysis
- Integration with external systems

### Compliance Features

**Recording Consent Tracking:**
- View which tenants have recording consent enabled
- Track consent status by guest
- Generate compliance reports

**Data Retention Policies:**
- Auto-delete voice recordings after X days
- Archive old messages
- Delete guest data on request

**GDPR/CCPA:**
- Export customer data in portable format
- Delete customer data completely
- Track data deletion requests

## Settings & Configuration

### Email Settings

**Navigation:** Admin → Settings → Email

Configure transactional emails:
- From address and name
- Reply-to address
- Logo and branding
- Email templates for:
  - Invoice notifications
  - Payment reminders
  - Tenant welcome
  - Suspension notices

**Test email:** Send test email to verify SMTP configuration

### Webhook Configuration

**Navigation:** Admin → Settings → Webhooks

View all registered webhooks:
- URL
- Events subscribed to
- Active status
- Last delivery status and time

**Manage webhooks:**
- Add new webhook
- Edit URL or events
- Disable webhook
- Resend failed webhooks
- View delivery logs

### Feature Flags

**Navigation:** Admin → Settings → Features

Enable/disable features per tenant:
- Voice AI calling
- WhatsApp integration
- SMS messaging
- Billing system
- iCal sync
- Advanced analytics

**Use cases:**
- Beta testing features with specific customers
- Gradual feature rollout
- A/B testing pricing or features

### API Keys

**Navigation:** Admin → Settings → API Keys

Manage administrative API access:
- Create new API key
- View key usage and limits
- Rotate keys
- Delete keys

Each key can be restricted to:
- Specific IP addresses
- Specific endpoints
- Read-only vs. write access

## Dashboard Customization

### Custom Widgets

Choose which metrics to display on dashboard:
1. Click "Customize Dashboard"
2. Toggle widgets on/off
3. Arrange in preferred order
4. Save

Available widgets:
- MRR gauge
- Customer acquisition chart
- Voice analytics
- Failed payments alert
- New support tickets
- System health status

### Alerts & Notifications

**Navigation:** Admin → Settings → Alerts

Configure when to send alerts:
- MRR drops below $X
- Churn rate exceeds X%
- API uptime below 99%
- Database query latency high
- Failed payment threshold

**Notification methods:**
- Email
- SMS (if configured)
- Slack (if integrated)

## Integrations

### Stripe Integration

**Navigation:** Admin → Settings → Stripe

Configure Stripe account:
- API key (from Stripe dashboard)
- Webhook secret (for payment notifications)
- Connected account (if using Stripe Connect)

**Test mode:**
- Enable test mode to use Stripe sandbox
- Use test card numbers (4242 4242 4242 4242)

### Slack Integration

**Navigation:** Admin → Settings → Slack

Connect Slack workspace:
1. Click "Connect Slack"
2. Authorize in Slack
3. Select channel for notifications
4. Choose notification types:
   - New customer signups
   - Failed payments
   - Support escalations
   - System alerts

### Other Integrations

- **Sentry** — Error tracking
- **Datadog** — Infrastructure monitoring
- **Sendgrid** — Transactional email
- **Twilio** — SMS notifications

## Troubleshooting

### Lost Admin Access

If you lose access to admin panel:
1. Use "Forgot Password" on login page
2. Click link in recovery email
3. Set new password
4. Sign in

If email doesn't work, contact technical support.

### Permission Denied Errors

If you see permission errors:
1. Check your role (go to Settings → Users)
2. Admin role has all permissions
3. Other roles may have restricted access
4. Ask another admin to update your role if needed

### Pricing Changes Not Taking Effect

When you update voice pricing:
- New customers see new price immediately
- Existing customers see new price at renewal
- To force immediate update, change customer plan manually

### Webhook Delivery Failures

If webhooks aren't being delivered:
1. Check webhook URL is correct (Admin → Settings → Webhooks)
2. Ensure your endpoint returns 200 status code
3. Verify webhook secret in your app matches
4. Check firewall isn't blocking inbound requests
5. View delivery logs to see error messages

## Support

For admin panel issues:
- Documentation: See `/docs` directory
- Issues: GitHub Issues
- Contact: support@yourdomain.com

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for deployment guide.
See **[SECURITY.md](SECURITY.md)** for security best practices.

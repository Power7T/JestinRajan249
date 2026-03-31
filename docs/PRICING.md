# Pricing Strategy & Billing

## Unit-Based Plans

HostAI uses unit-based pricing where each "unit" is one property/listing.

| Plan | Units | Price/Month | Cost per Unit | Best For |
|------|-------|-------------|---------------|----------|
| Starter | 1-5 | $20 | $4-20 | Solo hosts |
| Growth | 6-10 | $20 | $2-3.33 | Growing portfolios |
| Pro | 11-50 | $20 | $0.40-1.82 | Agencies & managers |

**Examples:**
- 1 property: Starter plan at $20/month
- 8 properties: Growth plan at $20/month ($2.50 per property)
- 25 properties: Pro plan at $20/month ($0.80 per property)

## Voice AI Add-Ons

Optional voice calling system available in 4 tiers:

### Light Tier - $39/month
- **Minutes included**: 100/month
- **Overage rate**: $0.049/min
- **Best for**: Small properties, occasional calls
- **Example**: 5 properties, 15-20 calls/month

### Standard Tier - $79/month
- **Minutes included**: 300/month
- **Overage rate**: $0.049/min
- **Best for**: Growing businesses, 50-75 calls/month
- **Example**: 10-15 properties, regular use

### Professional Tier - $129/month
- **Minutes included**: 750/month
- **Overage rate**: $0.049/min
- **Best for**: Multi-property managers, 150-200 calls/month
- **Example**: 20-30 properties, heavy use

### Unlimited Tier - $199/month
- **Minutes included**: Unlimited
- **Overage rate**: Free
- **Best for**: Enterprise, 500+ calls/month
- **Example**: Property management companies

## Cost Breakdown

### Infrastructure Costs (per call)
```
Twilio (inbound call): $0.015
Deepgram (speech-to-text): $0.0043
OpenAI (LLM response): $0.005
ElevenLabs (text-to-speech): $0.020
─────────────────────────────
Total per 1-min call: ~$0.044
```

### Pricing Strategy

**Goal:** Maintain 70%+ gross margin on voice add-ons

```
Average call: 2.5 minutes
Cost per call: ~$0.11
Tier revenue per 100 calls:

Light ($39):   100 calls = 100 min = $4.40 cost = 88% margin ✅
Standard ($79): 300 calls = 750 min = $33 cost = 58% margin ✅
Professional ($129): 750 calls = 1875 min = $82.50 cost = 36% margin ✅
Unlimited ($199): unlimited calls = high margin at scale ✅
```

## Overage Pricing

When customers exceed their included minutes:

**Overage rate:** $0.049 per minute (5x raw cost)

**Psychology:**
- Discourages excessive use (prevents abuse)
- Encourages tier upgrades (if frequently overaging, upgrade)
- Maintains profitability on heavy users

**Example overage scenario:**
```
Customer on Standard ($79, 300 minutes)
Uses 350 minutes in a month

Charges:
- Base: $79
- Overage: 50 min × $0.049 = $2.45
- Total: $81.45

Next month: Customer upgrades to Professional ($129)
(More minutes + same overage rate = better value)
```

## Margin Analysis

### Per Tier Profitability

**Light Tier** - Conservative pricing, high margin:
- Cost basis: $4.40/100 calls
- Selling price: $39/month
- Margin: 88%
- Target customers: Price-sensitive, low volume

**Standard Tier** - Balanced pricing:
- Cost basis: $33/300 calls
- Selling price: $79/month
- Margin: 58%
- Target customers: Growing businesses

**Professional Tier** - Volume discount:
- Cost basis: $82.50/750 calls
- Selling price: $129/month
- Margin: 36%
- Target customers: Multi-property

**Unlimited Tier** - Enterprise:
- Cost basis: Varies (avg $200+ at high usage)
- Selling price: $199/month
- Margin: Negative at low usage, positive at scale
- Target customers: High-volume users (1000+ calls/month)

## Pricing Adjustments

Admin can adjust pricing in real-time via admin panel without code changes:

**Available adjustments:**
- Monthly price per tier
- Overage rate per minute
- Minutes included per tier
- Surge threshold and multiplier

**Margin protection:**
- Admin panel shows live margin calculations
- Warnings if pricing would drop below 70%
- Audit trail of all price changes

## Revenue Model

### Monthly Revenue Calculation

```
Monthly Revenue = (Unit plans) + (Voice add-on subscriptions) + (Overage charges)

Example portfolio:
  - 10 Starter plan customers × $20 = $200
  - 15 Standard voice add-ons × $79 = $1,185
  - 5 Professional voice add-ons × $129 = $645
  - Overage charges (est): $300
  ─────────────────────────────
  Total MRR: $2,330
  Gross margin: ~75%
```

### Unit Economics

**Customer acquisition cost:** $150 (typical)
**Lifetime value:** $2,400 (1-year average customer)
**LTV:CAC ratio:** 16:1 (excellent)

**Payback period:** ~4-5 weeks

## Promotional Offers

**Available promotions** (applied via admin):
- Discount percentage (e.g., 20% off)
- Duration (e.g., 3 months)
- Tier limitation (e.g., only for new customers)

**Usage tracking:**
- Track discount acceptance rate
- Monitor impact on churn
- Measure CAC impact

## Billing Mechanics

### Invoice Generation
- Monthly invoices generated on subscription anniversary
- Email sent to customer with invoice PDF
- Downloadable from customer portal

### Payment Processing
- Stripe integration for automated payments
- Retry logic for failed payments (3 attempts)
- Payment notifications via email

### Upgrades/Downgrades
- **Upgrade**: Prorated charge for remainder of month
- **Downgrade**: Prorated credit toward next invoice
- **Effective date**: Immediate or at end of month (configurable)

## Analytics

### Revenue Metrics (Admin Dashboard)
- **MRR** (Monthly Recurring Revenue): Total subscription revenue
- **ARR** (Annual Recurring Revenue): MRR × 12
- **Churn rate**: % of customers leaving per month
- **Net retention**: MRR growth after churn

### Cost Metrics
- **COGS** (Cost of Goods Sold): API costs per customer
- **Gross margin**: (Revenue - COGS) / Revenue
- **CAC** (Customer Acquisition Cost): Sales spend / new customers
- **LTV** (Lifetime Value): Average customer revenue over lifetime

### Tier Metrics
- Customers per tier
- ARPU (Average Revenue Per User) per tier
- Churn rate per tier
- Upgrade/downgrade rates between tiers

## Price Changes

### Making Price Changes
1. Go to Admin → Voice Pricing
2. Update tier price, overage rate, or minutes
3. Changes apply to:
   - New customers immediately
   - Existing customers at renewal date
4. Audit log tracks all changes

### Communication to Customers
- Send 30-day notice before price increase
- Explain value increase
- Offer month-to-month option
- Grandfather existing customers (optional)

## Competitive Pricing

### Market Positioning

**Compared to alternatives:**
- **Twilio IVR**: $0.025/min (no AI, basic routing)
- **OpenAI**: $15/month for API (no calling)
- **Intercom**: $199+/month (no voice)

**Our advantage:** Complete solution (calling + AI + analytics) at competitive price

### Pricing Strategy
- **Penetration pricing**: Enter market aggressively
- **Value-based pricing**: Price based on value delivered
- **Tiered pricing**: Options for different customer sizes

## Future Pricing Plans

**Planned for Q2 2026:**
- Usage-based pricing (pay per call)
- Enterprise contracts (custom pricing)
- Volume discounts for agencies
- International pricing variations

---

**Note:** All prices and margins are examples. Actual prices configurable in admin panel. See ADMIN.md for pricing management details.

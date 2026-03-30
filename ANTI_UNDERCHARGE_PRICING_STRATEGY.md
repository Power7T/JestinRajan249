# Anti-Undercharge Pricing Strategy
## Ensuring Sustainable Margins & Preventing Low-Margin Customers

**Date:** March 30, 2026
**Goal:** Design pricing that ensures profit at ALL tier levels, with built-in protections

---

## PART 1: Cost Structure Analysis

### Fixed Monthly Costs (Per Tenant)
| Item | Monthly Cost | Notes |
|------|-------------|-------|
| Database storage | $0.50 | PostgreSQL on AWS RDS |
| API keys management | $0.20 | Encryption, storage |
| Support/email replies | $2-5 | Estimated per customer |
| Infrastructure baseline | $3-5 | Server, networking |
| **Total Fixed** | **$6-10** | Minimum per customer |

### Variable Costs (Per Unit/Property)
| Item | Cost | Notes |
|------|------|-------|
| Storage (iCal, drafts, files) | $0.30/unit | ~1GB per unit |
| Email sending (Postmark/SendGrid) | $0.10/unit | ~500 emails/month average |
| SMS sending (Twilio SMS) | $0.15/unit | ~30 SMS/unit/month |
| WhatsApp API (Meta Cloud) | $0.80/unit | ~5 msgs/unit/day |
| **Total Variable** | **$1.35/unit** | Without voice |

### Voice AI Calling Costs (Per Minute)
| Component | Cost/Min | Qty | Total |
|-----------|----------|-----|-------|
| Twilio voice | $0.0100 | 1 min | $0.0100 |
| Deepgram STT | $0.0043 | 1 min | $0.0043 |
| OpenAI GPT-4o-mini | $0.0005 | 1 call | $0.0005 |
| ElevenLabs TTS | $0.0030 | 1 min | $0.0030 |
| Storage (recording) | $0.0001 | 1 min | $0.0001 |
| **Total Voice Cost** | **$0.0179/min** | — | ~**$0.018/min** |

---

## PART 2: Break-Even Analysis

### Scenario 1: Starter Plan (3 units, no voice)
```
Revenue:
  Base: $25/mo
  Units: 3 × $10 = $30/mo
  Total Revenue: $55/mo

Costs:
  Fixed: $8/mo
  Variable: 3 × $1.35 = $4.05/mo
  Total Costs: $12.05/mo

PROFIT: $55 - $12 = $43/mo ✅ (78% margin)
```

### Scenario 2: Starter Plan (3 units, WITH voice overages)
```
Revenue:
  Base: $25/mo
  Units: 3 × $10 = $30/mo
  Voice add-on: $0 (if included or not purchased)
  Total Revenue: $55/mo

Costs:
  Fixed: $8/mo
  Variable: 3 × $1.35 = $4.05/mo
  Voice (100 mins): 100 × $0.018 = $1.80/mo
  Total Costs: $13.85/mo

PROFIT: $55 - $14 = $41/mo ✅ (75% margin)
```

### Scenario 3: Growth Plan (8 units, light voice use)
```
Revenue:
  Base: $45/mo
  Units: 8 × $8 = $64/mo
  Voice add-on: $0 (not purchased)
  Total Revenue: $109/mo

Costs:
  Fixed: $8/mo
  Variable: 8 × $1.35 = $10.80/mo
  Total Costs: $18.80/mo

PROFIT: $109 - $19 = $90/mo ✅ (83% margin)
```

### Scenario 4: Growth Plan (8 units, HEAVY voice use - 500 mins)
```
Revenue:
  Base: $45/mo
  Units: 8 × $8 = $64/mo
  Voice add-on: $59/mo (500 mins included)
  Total Revenue: $168/mo

Costs:
  Fixed: $8/mo
  Variable: 8 × $1.35 = $10.80/mo
  Voice: 500 × $0.018 = $9.00/mo
  Total Costs: $27.80/mo

PROFIT: $168 - $28 = $140/mo ✅ (83% margin)
```

### Scenario 5: Pro Plan (20 units, unlimited voice)
```
Revenue:
  Base: $75/mo
  Units: 20 × $6 = $120/mo
  Voice (unlimited): included
  Total Revenue: $195/mo

Costs:
  Fixed: $8/mo
  Variable: 20 × $1.35 = $27/mo
  Voice (estimate 200 mins average): 200 × $0.018 = $3.60/mo
  Total Costs: $38.60/mo

PROFIT: $195 - $39 = $156/mo ✅ (80% margin)
```

**Key Finding:** All scenarios maintain 75%+ margins. ✅

---

## PART 3: Anti-Undercharge Mechanisms

### 1. **Minimum Spend Requirement**
```
Rule: Prevent customers from paying less than cost-of-service

Per-Customer Minimum: $35/mo
├─ Covers fixed costs ($8) + overhead + support
├─ Covers any tier, any unit count
└─ Enforced at signup (smallest package: Starter 1-unit = $35)

Implementation:
  if (base_price + (units * per_unit_price)) < 35:
      final_price = 35
```

### 2. **Usage-Based Billing with Hard Caps**
```
Voice Minutes — Strict Overages

Starter Base: 0 mins/mo included (must add-on)
Growth Base: 300 mins/mo included
Pro Base: Unlimited

Overage Pricing: $0.029/minute (60% markup over cost of $0.018)
├─ Automatically charged on day 1 of next month
├─ Alert at 80% of limit
├─ Mandatory review at 100%
└─ Hard cap: Auto-pause calls at 150% of limit

Example: Growth customer uses 450 mins
  Included: 300 mins = $0 cost
  Overage: 150 mins × $0.029 = $4.35 cost
  Total charge: +$4.35 to next invoice
```

### 3. **Add-On Pricing (High-Margin Features)**
```
Premium Add-Ons (Optional, High Margin)

Smart Voice Routing ($19/mo)
├─ Route calls by guest type (VIP, first-time, repeat)
├─ Cost: $0 (logic only)
├─ Margin: 100%

Post-Call SMS Auto-Send ($9/mo)
├─ Auto-send confirmation/next-steps via SMS
├─ Cost: ~$0.10/month (infrastructure)
├─ Margin: 98%

Guest Sentiment Analysis ($29/mo)
├─ AI analyzes call sentiment, flags issues
├─ Cost: $0.05/month (extra OpenAI calls)
├─ Margin: 99%

Priority 24h Support ($49/mo)
├─ Phone support, Slack integration
├─ Cost: $15-20/month (support time)
├─ Margin: 59%

Advanced Analytics Dashboard ($39/mo)
├─ KPI dashboards, forecasting, benchmarking
├─ Cost: $1-2/month (infrastructure)
├─ Margin: 95%

Recording Storage Overage ($0.10/GB)
├─ Beyond 10GB included per unit
├─ Cost: $0.05/GB AWS
├─ Margin: 50%
```

### 4. **Team Member Licensing (Prevents Free Riders)**
```
User Seat Pricing

Starter: 2 users included
├─ Additional users: $9/user/mo
└─ Prevents: Sharing single account, avoiding seat licensing

Growth: 4 users included
├─ Additional users: $7/user/mo
└─ Slightly better rate due to higher plan cost

Pro: 8 users included
├─ Additional users: $5/user/mo
└─ Encourages team collaboration without free riders

Implementation:
  users_included = plan_config['users_included']
  extra_users = max(0, active_users - users_included)
  extra_user_charge = extra_users * user_price
```

### 5. **Property Scaling Locks (Prevent Gaming)**
```
Unit Count Locks - Force Tier Upgrade

Starter: 5 units max
├─ Can't add 6th unit without upgrading to Growth
├─ Prevents: Cramming into cheapest tier

Growth: 15 units max
├─ Can't add 16th unit without upgrading to Pro
├─ Prevents: Using cheaper-per-unit tier too long

Pro: 50 units max
├─ Can't add 51st unit without enterprise agreement
├─ Ensures: Huge hosts negotiate custom rates

Implementation:
  units = count_active_units(tenant_id)
  max_units = plan_config[current_plan]['max_units']
  if units > max_units:
      block_new_guests()  # Or auto-upgrade
```

### 6. **Surge Pricing for Abuse Prevention**
```
Rate-Based Usage Surcharges

If monthly voice minutes exceed 50% of limit:
  Apply 25% surcharge to per-minute costs
  Example: 400 mins in Growth (300 included) = 100 overage × $0.029 × 1.25 = $3.63

If SMS exceeds 1000/unit/month:
  SMS normal: $0.007/SMS
  SMS surge (>1000): $0.012/SMS (70% increase)
  Prevents: Bot-like behavior, scrapers, abuse

If WhatsApp exceeds 200 msgs/unit/day:
  Meta charges per-conversation, encourage higher plan

Implementation:
  monthly_overage_pct = (actual_usage - limit) / limit
  if monthly_overage_pct > 50%:
      overage_rate *= 1.25  # Surge pricing
```

### 7. **Contract Lock-In (Annual Discount with Penalty)**
```
Annual Billing Options

Monthly: Pay-as-you-go, cancel anytime

Annual (15% discount):
├─ Must commit to 12 months
├─ Early cancel fee: Remaining months × 50%
├─ Example: Cancel at month 6 = 6 × 50% = refund 3 months only
├─ Benefits: Predictable revenue, customer commitment
└─ Discount still profitable (85% of normal = ~85-90% margin vs 83%)

Annual + Upfront (20% discount):
├─ Must prepay full year (cash flow benefit)
├─ Same early cancel fee
├─ Benefits: Zero churn for 12 months, better pricing

Example: Growth plan
  Monthly: 12 × $109 = $1,308/year
  Annual: 12 × $109 × 0.85 = $1,111/year (saves $197)
  You: Still make $900/year profit (vs $1,080 monthly)
```

### 8. **Tiered Data Limits (Storage/API Quotas)**
```
API Call Limits (Per Tier)

Starter:
  ├─ 1,000 API calls/month (host dashboard, drafts, etc.)
  ├─ 1GB file storage (iCal, uploads, recordings)
  └─ Overage: $0.01/call, $0.50/GB/month

Growth:
  ├─ 10,000 API calls/month
  ├─ 5GB file storage
  └─ Overage: $0.005/call, $0.30/GB/month

Pro:
  ├─ 100,000 API calls/month
  ├─ 50GB file storage
  └─ Overage: $0.002/call, $0.20/GB/month

Implementation: Meter API calls, warn at 80%, charge overage
```

### 9. **Feature Sunset & Paid Tier Promotion**
```
Planned Feature Deprecations

Example: iCal sync (low-cost feature)
  Today: Included in all plans
  Month 1-3: Include in Growth/Pro only (remove from Starter)
  Month 4+: $9/mo add-on for Starter tier

  Reduces: Free feature consumption
  Drives: Upgrade to Growth

Example: Guest contacts limit
  Starter: 100 contacts max (free)
  Growth: 1,000 contacts (included)
  Pro: Unlimited
  Overage: $0.05 per contact stored
```

### 10. **Enterprise/Custom Pricing (No Race to Bottom)**
```
Customers with 50+ units → No standard pricing

For any prospect requesting <$150/mo total:
├─ DO NOT discount base pricing
├─ Instead: Limit features to lower tier
├─ Example: "You want Pro at Starter price?
│           How about Growth features at Growth price?"
└─ Prevents: Custom discounts that undermine tier system

Custom Pricing Only For:
  ├─ $2,000+/mo annual contracts
  ├─ Multi-year commitments (3+ years)
  ├─ Enterprise features (SSO, custom integrations, SLA)
  └─ <5% of customer base max
```

---

## PART 4: Pricing Table (Anti-Undercharge Design)

### RECOMMENDED: Strategy 1 with Anti-Undercharge Features

```
╔══════════════════════════════════════════════════════════════════════╗
║                          STARTER PLAN                               ║
╠══════════════════════════════════════════════════════════════════════╣
║ Base Price: $25/month                                                ║
║ Per Unit: $10/unit/month (1-5 units max)                             ║
║ Minimum Invoice: $35/month (enforced)                                ║
║                                                                      ║
║ INCLUDED:                                                            ║
║  ✓ Web dashboard & email drafts                                     ║
║  ✓ iCal calendar sync                                                ║
║  ✓ Team members (2 seats, +$9/each extra)                           ║
║  ✓ Guest contacts (100 max, +$0.05 overage)                        ║
║  ✓ 1 GB file storage (iCal, uploads) +$0.50/GB                     ║
║  ✓ 1,000 API calls/month +$0.01/call overage                       ║
║                                                                      ║
║ NOT INCLUDED:                                                       ║
║  ✗ SMS (add $0.007/SMS or upgrade to Growth)                       ║
║  ✗ WhatsApp (upgrade to Growth required)                            ║
║  ✗ Voice calling (add $59/mo for 500 mins)                         ║
║  ✗ Guest sentiment analysis (add $29/mo)                           ║
║  ✗ Priority support (add $49/mo)                                    ║
║                                                                      ║
║ EXAMPLE: 3 units, 1 extra user, 50 SMS/month                        ║
║  Base: $25 + (3 × $10) + $9 + (50 × $0.007) = $64.35/month ✓       ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║                          GROWTH PLAN                                 ║
╠══════════════════════════════════════════════════════════════════════╣
║ Base Price: $45/month                                                ║
║ Per Unit: $8/unit/month (6-15 units max)                             ║
║                                                                      ║
║ INCLUDED:                                                            ║
║  ✓ Everything in Starter PLUS:                                      ║
║  ✓ SMS via Twilio (unlimited) — $0.007/SMS                         ║
║  ✓ WhatsApp Cloud API (limited) — $0.80/unit cost                  ║
║  ✓ Team members (4 seats, +$7/each extra)                          ║
║  ✓ Guest contacts (1,000 max)                                      ║
║  ✓ 5 GB file storage                                                ║
║  ✓ 10,000 API calls/month                                          ║
║  ✓ Voice calling (300 mins/month included)                         ║
║    - Additional mins: $0.029/min (overage surcharge)                ║
║    - 50%+ overage: 25% surge pricing applied                       ║
║  ✓ Advanced analytics dashboard                                     ║
║  ✓ Audit logs (security)                                            ║
║                                                                      ║
║ UPSELL OPPORTUNITIES:                                               ║
║  + Smart voice routing: +$19/mo                                     ║
║  + Post-call SMS: +$9/mo                                            ║
║  + Guest sentiment: +$29/mo                                         ║
║  + Priority support: +$49/mo                                        ║
║                                                                      ║
║ EXAMPLE: 8 units, 1 extra user, 400 voice mins                      ║
║  Base: $45 + (8 × $8) + $7 + voice overage                          ║
║  Voice: 300 included, 100 overage × $0.029 = +$2.90                ║
║  Total: $114.90/month ✓ (Excellent margin despite voice)            ║
╚══════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════╗
║                           PRO PLAN                                   ║
╠══════════════════════════════════════════════════════════════════════╣
║ Base Price: $75/month                                                ║
║ Per Unit: $6/unit/month (16-50 units max)                            ║
║                                                                      ║
║ INCLUDED:                                                            ║
║  ✓ Everything in Growth PLUS:                                       ║
║  ✓ Voice calling (UNLIMITED) — included in price                   ║
║  ✓ Team members (8 seats, +$5/each extra)                          ║
║  ✓ 50 GB file storage                                               ║
║  ✓ 100,000 API calls/month                                         ║
║  ✓ Admin dashboard (KPIs, cost analysis)                           ║
║  ✓ Priority support (24h response)                                  ║
║  ✓ Guest sentiment analysis                                         ║
║  ✓ Smart voice routing                                              ║
║                                                                      ║
║ ENTERPRISE FEATURES:                                                ║
║  + Custom integrations: Quote                                       ║
║  + SSO/2FA: +$99/mo                                                 ║
║  + Dedicated Slack channel: +$199/mo                                ║
║  + SLA (99.9% uptime): +$149/mo                                    ║
║                                                                      ║
║ EXAMPLE: 20 units, 2 extra users, 500 voice mins                    ║
║  Base: $75 + (20 × $6) + (2 × $5) = $175/month                     ║
║  Voice: Unlimited (included, even with high usage)                  ║
║  Total: $175/month (No overage, clean invoice) ✓                   ║
║  Margin: ~80% (still excellent)                                     ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## PART 5: Margin Analysis at Different Customer Profiles

### Profile 1: Small Host (Starter, 2 units, light usage)
```
Revenue: $25 + (2 × $10) = $45/mo
Costs: $8 + (2 × $1.35) = $10.70/mo
Margin: $34.30/mo (76%) ✓
→ Use minimum fee to prevent undercharge
```

### Profile 2: Growing Host (Growth, 10 units, moderate voice)
```
Revenue: $45 + (10 × $8) + voice add-on $59 = $164/mo
Costs: $8 + (10 × $1.35) + (400 mins × $0.018) = $21.30/mo
Margin: $142.70/mo (87%) ✓
→ Excellent margin, voice add-on pays for itself
```

### Profile 3: Power User (Pro, 25 units, heavy voice)
```
Revenue: $75 + (25 × $6) = $225/mo
Costs: $8 + (25 × $1.35) + (2000 mins voice × $0.018) = $79/mo
Margin: $146/mo (65%) ✓
→ Still profitable even with unlimited voice
```

### Profile 4: Enterprise (Custom 50+ units, unlimited everything)
```
Revenue: Minimum $200-500/mo (negotiated)
Costs: $8 + (50 × $1.35) + (5000 mins × $0.018) = $200/mo
Margin: $0-300/mo (0-60%) ⚠️
→ NO custom pricing <$300/mo. Only do custom if:
   ├─ 3-year contract
   ├─ Annual prepayment
   └─ Specific SLA/integration requirements
```

---

## PART 6: Pricing Rules (Policy)

### Rule 1: Never Discount Base Pricing
```
✗ DO NOT offer 20% off base price
✗ DO NOT offer "special rate" for long-term customers
✗ DO NOT bundle discounts across products

✓ DO offer annual prepayment discount (20%)
✓ DO offer feature upgrades (upsell)
✓ DO offer add-on discounts (team seats, storage)
```

### Rule 2: Usage Overages Are Non-Negotiable
```
If customer exceeds limits:
  ✓ Charge the overage (no exceptions)
  ✗ Do not "waive" overages for loyal customers
  ✓ Offer to upgrade to next tier instead

Example: "You've used 600 mins on Growth plan.
         Consider upgrading to Pro (unlimited)
         at only +$30/mo for better savings."
```

### Rule 3: Enterprise Only for Strategic Deals
```
Do NOT give enterprise pricing unless:
  ✓ Annual contract value >$3,000
  ✓ Multi-year commitment (3+ years)
  ✓ Specific feature request (custom integration)
  ✓ Unique use case (API reseller, white-label)

Do NOT:
  ✗ Match competitor pricing on per-unit basis
  ✗ Create custom SKUs for individual customers
  ✗ Offer indefinite discounts
```

### Rule 4: Tier Up, Not Down
```
If customer outgrows tier:
  ✓ Auto-upgrade at next billing cycle
  ✓ Pro-rate the difference (charge difference)
  ✗ Do not allow customers to stay in lower tier

Implementation:
  if units > tier_max:
      auto_upgrade_to_next_tier()
      invoice_prorated_difference()
```

### Rule 5: Minimum Spend Enforced
```
No customer should pay less than $35/mo ever.

If calculation results in <$35:
  Charge: $35/mo

This covers:
  ├─ Fixed costs ($8)
  ├─ Support overhead ($5)
  ├─ Payment processing ($1)
  └─ Profit margin ($21 = 60%)
```

---

## PART 7: Red Flags (When You're Undercharging)

🚩 **Red Flag 1: Customer uses voice heavily but pays <$100/mo**
   - Voice costs $0.018/min minimum
   - 500 mins = $9 cost, should charge $59/mo add-on
   - If not charging: **FIX IT**

🚩 **Red Flag 2: Growing customer stays in lower tier >6 months**
   - Should have 3-4 unit increases/year to trigger upgrade
   - If not: Either underpriced or customer not growing
   - **INVESTIGATE**

🚩 **Red Flag 3: Freemium plan has >20% of users**
   - Free plan should be <10% of customer base
   - If >20%: Free plan is too generous
   - **REDUCE FREE FEATURES**

🚩 **Red Flag 4: Add-ons purchased by <5% of tier**
   - Add-ons should be ~20-30% adoption
   - If <5%: Either priced too high or not needed
   - **REPOSITION OR REMOVE**

🚩 **Red Flag 5: Enterprise customers at $100-150/mo**
   - Enterprise should be $300+/mo minimum
   - If less: Custom pricing too low
   - **RENEGOTIATE ON RENEWAL**

🚩 **Red Flag 6: Margin trending <70%**
   - Healthy SaaS = 70-85% margin
   - <70%: Costs too high, prices too low
   - **URGENT: AUDIT PRICING**

---

## PART 8: Implementation Checklist

- [ ] Update web/billing.py with new PLAN_INFO
- [ ] Update database PlanConfig with base_fee and per_unit_fee
- [ ] Add minimum_monthly_fee = 35 to PlanConfig
- [ ] Create Stripe price IDs for all SKUs
- [ ] Implement usage metering for voice minutes
- [ ] Implement overage calculation logic
- [ ] Add alert system for 80% of limits
- [ ] Create auto-upgrade logic for tier limits
- [ ] Build admin dashboard to track margins
- [ ] Test 10 customer scenarios to verify margins
- [ ] Write pricing policy document for support team
- [ ] Train sales on "no discounts" policy
- [ ] Monitor margins monthly (target: 75%+)

---

## Summary

**This strategy ensures:**
1. ✅ No customer pays less than $35/mo
2. ✅ Every tier maintains 70%+ margin
3. ✅ Overages are profitable (+60% markup)
4. ✅ Add-ons drive revenue (+100% margin)
5. ✅ Annual contracts lock in customers
6. ✅ Enterprise pricing stays >$300/mo minimum
7. ✅ Voice feature stays profitable despite costs
8. ✅ Scaling doesn't destroy margins

**Result:** Sustainable, profitable SaaS with healthy unit economics.

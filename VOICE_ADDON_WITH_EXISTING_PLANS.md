# Voice AI Add-Ons — Works with All Existing Plans

## TL;DR
- **Keep all existing plans as-is** (Free, Baileys, Meta, SMS, Pro)
- **Add voice AI as optional add-on** for any plan
- **4 voice tiers:** Light ($39), Standard ($79), Professional ($129), Unlimited ($199)
- **Maintain 70%+ margins** with aggressive pricing (9.6x-21.7x markup)

---

## How Voice Add-Ons Integrate with Existing Plans

### Free Plan
```
Base: $0/month
+ Voice Light: $39/month     = $39/month total ✓
+ Voice Standard: $79/month  = $79/month total ✓
+ Voice Professional: $129/month = $129/month total ✓
+ Voice Unlimited: $199/month = $199/month total ✓
```

### Baileys Plan ($19/mo)
```
Base: $19/month (WhatsApp local bot)
+ Voice Light: $39/month     = $58/month total ✓
+ Voice Standard: $79/month  = $98/month total ✓
+ Voice Professional: $129/month = $148/month total ✓
+ Voice Unlimited: $199/month = $218/month total ✓
```

### Meta Cloud Plan ($29/mo)
```
Base: $29/month (WhatsApp Cloud API)
+ Voice Light: $39/month     = $68/month total ✓
+ Voice Standard: $79/month  = $108/month total ✓
+ Voice Professional: $129/month = $158/month total ✓
+ Voice Unlimited: $199/month = $228/month total ✓
```

### SMS Plan ($19/mo)
```
Base: $19/month (Twilio SMS)
+ Voice Light: $39/month     = $58/month total ✓
+ Voice Standard: $79/month  = $98/month total ✓
+ Voice Professional: $129/month = $148/month total ✓
+ Voice Unlimited: $199/month = $218/month total ✓
```

### Pro Plan ($49/mo)
```
Base: $49/month (All channels)
+ Voice Light: $39/month     = $88/month total ✓
+ Voice Standard: $79/month  = $128/month total ✓
+ Voice Professional: $129/month = $178/month total ✓
+ Voice Unlimited: $199/month = $248/month total ✓
```

---

## Pricing Table — All Combinations

| Plan | Base | + Voice Light | + Voice Std | + Voice Pro | + Voice Unlimited |
|------|------|---------------|------------|------------|-----------------|
| **Free** | $0 | **$39** | **$79** | **$129** | **$199** |
| **Baileys** | $19 | **$58** | **$98** | **$148** | **$218** |
| **Meta Cloud** | $29 | **$68** | **$108** | **$158** | **$228** |
| **SMS** | $19 | **$58** | **$98** | **$148** | **$218** |
| **Pro** | $49 | **$88** | **$128** | **$178** | **$248** |

---

## Voice Add-On Details

### Voice Light — $39/month
```
For: Testing, small properties (1-3 units)
Includes:
  • 100 AI voice calls/month
  • Deepgram speech-to-text
  • OpenAI GPT-4o responses
  • ElevenLabs text-to-speech
  • Call recording & storage
  • Sentiment detection

Overage: $0.049/min (charge if >100 mins/month)
Cost basis: $1.80/month
Markup: 21.7x (excellent)
```

### Voice Standard — $79/month ⭐ MOST POPULAR
```
For: Growing hosts (6-12 units, ~20 calls/day)
Includes:
  • 300 AI voice calls/month
  • Smart routing (first-time vs repeat guests)
  • Call analytics & insights
  • Guest sentiment tracking
  • Priority call handling
  • All Light features

Overage: $0.049/min (charge if >300 mins/month)
Surge: 15% extra if >150 mins over limit
Cost basis: $5.40/month
Markup: 14.6x (excellent)

Use case: 8 units × 20 calls/day × 4 mins = 640 mins
  Included: 300 mins
  Overage: 340 mins × $0.049 = $16.66
  Total cost: $79 + $16.66 = $95.66 (better to upgrade to Professional)
```

### Voice Professional — $129/month
```
For: Large hosts (15-25 units, ~30 calls/day)
Includes:
  • 750 AI voice calls/month
  • Advanced voice routing by guest history
  • Real-time sentiment alerts
  • Automatic escalation to humans
  • Post-call summary via SMS
  • Call quality analytics
  • White-glove onboarding
  • All Standard features

Overage: $0.049/min (rare at 750 mins)
Cost basis: $13.50/month
Markup: 9.6x (very good)

Use case: 20 units × 30 calls/day × 4 mins = 2,400 mins
  Included: 750 mins
  Overage: 1,650 mins × $0.049 = $80.85
  Total: $129 + $80.85 = $209.85
  Better to upgrade to Unlimited (-$10/month)
```

### Voice Unlimited — $199/month
```
For: Enterprise hosts (25+ units, high call volume)
Includes:
  • UNLIMITED AI voice calls
  • Dedicated support channel (Slack)
  • Custom voice prompts & training
  • Advanced analytics & reporting
  • API access for integrations
  • SLA: 99.9% uptime guarantee
  • Priority engineering support
  • All Professional features

Overage: None (everything included)
Cost basis: ~$36/month (estimate for 2,000 mins)
Markup: 5.5x (acceptable for unlimited)

Use case: 30 units, heavy usage (5,000 mins/month)
  All included, no surprises, clean invoice
  Margin still 70%+ even with heavy usage
```

---

## Margin Analysis with Existing Plans

### Example 1: Small Host (Meta Cloud + Voice Light)
```
Revenue: $29 + $39 = $68/month
Costs:
  Plan: Meta API cost ~$0.50/month
  Voice: 100 mins × $0.018 = $1.80
  Fixed: $8/month
  Total costs: $10.30
Margin: $57.70/month (85%) ✓✓
```

### Example 2: Growing Host (Pro + Voice Standard)
```
Revenue: $49 + $79 = $128/month
Costs:
  Plan: All channels cost ~$3/month
  Voice: 300 mins × $0.018 = $5.40
  Fixed: $8/month
  Total costs: $16.40
Margin: $111.60/month (87%) ✓✓
```

### Example 3: Power Host (Pro + Voice Unlimited + 3,000 mins overage)
```
Revenue: $49 + $199 = $248/month
Costs:
  Plan: All channels ~$3/month
  Voice: 3,000 mins × $0.018 = $54/month
  Fixed: $8/month
  Total costs: $65/month
Margin: $183/month (74%) ✓
```

---

## Implementation Checklist

- [x] Define VOICE_ADD_ON_PRICING dict in web/billing.py
- [x] 4 tiers with aggressive pricing (9.6x-21.7x markup)
- [x] Overage pricing at $0.049/min (170% markup)
- [ ] Add voice add-on selector to signup/upgrade flow
- [ ] Create Stripe products for each voice tier
- [ ] Update pricing page to show voice add-on options
- [ ] Add billing calculation logic for overage charges
- [ ] Track monthly voice usage per tenant
- [ ] Send overage alerts at 80% of monthly limit
- [ ] Auto-charge overages on next billing cycle
- [ ] Create self-service upgrade flow in dashboard

---

## Key Points

✅ **Old plans unchanged** — Free, Baileys, Meta, SMS, Pro stay the same
✅ **Voice is optional** — Any customer can add to any plan
✅ **Strong margins** — 70%+ profit on all combinations
✅ **Profitable growth** — Even free plan + voice = $39+ MRR
✅ **Simple add-on UX** — "Choose plan + choose voice tier"
✅ **Flexible pricing** — Light tier for experimentation, Unlimited for large hosts
✅ **Overage protection** — $0.049/min discourages abuse, encourages upgrades

---

## Next: Implementation Steps

1. **Stripe Setup**
   - Create 4 voice products (Light, Std, Pro, Unlimited)
   - Map to API keys: STRIPE_PRICE_VOICE_LIGHT, etc.

2. **Signup Flow**
   - Step 1: Choose base plan (Free/Baileys/Meta/SMS/Pro)
   - Step 2: Optional — Add voice? (Light/Standard/Professional/Unlimited)
   - Step 3: Review total pricing

3. **Dashboard**
   - Show current voice usage (if any)
   - Upgrade/downgrade voice tier button
   - Warning at 80% of monthly limit
   - Usage analytics

4. **Billing**
   - Track voice minutes per customer
   - Calculate overages monthly
   - Add overage charges to next invoice
   - Send usage report email

# Admin Pricing Panel — Complete Guide

## Overview

The admin panel now includes a **Voice Pricing Management** dashboard where you can edit all pricing parameters in real-time without code changes or redeployment.

---

## Access Admin Pricing Panel

1. **Login as admin** to your account (must have `is_admin(email)` flag set)
2. **Click Admin Panel** in dashboard sidebar
3. **Select "Voice Pricing"** from admin menu
4. **Edit any pricing parameters** and click "Update Pricing"

**URLs:**
- Dashboard: `/admin/voice-pricing`
- API endpoint: `/api/admin/voice-pricing` (JSON)

---

## What You Can Edit

### For Each Voice Tier (Light, Standard, Professional, Unlimited):

| Parameter | Current | Description | Example |
|-----------|---------|-------------|---------|
| **Monthly Price** | $39-$199 | What customers pay/month | $49 (for Light) |
| **Overage Rate** | $0.049 | Charge per extra minute | $0.059 (to increase) |
| **Minutes Included** | 100/300/750/∞ | Base allocation | 150 (for Light) |
| **Surge Threshold** | 50% | When to apply surge pricing | 40% (apply earlier) |
| **Surge Multiplier** | 1.15x | Extra charge if over threshold | 1.25x (+25% premium) |

---

## Examples: Adjusting Pricing

### Scenario 1: Increase Light Tier from $39 → $49

1. Go to `/admin/voice-pricing`
2. Find "Voice Light" card
3. Change "Monthly Price (USD)" from `39` → `49`
4. Click "Update Pricing"
5. ✅ Light tier is now $49/month for all new customers
6. Audit log: "Admin {email} updated voice pricing for light tier"

### Scenario 2: Increase Overage Protection

Problem: Too many customers using overages without upgrading.

Solution:
1. Go to `/admin/voice-pricing`
2. **For all tiers**: Change "Overage Per Min (USD)" from `0.049` → `0.079`
3. Optionally lower "Surge Threshold" from `50%` → `40%` (apply surge earlier)
4. Set "Surge Multiplier" from `1.15x` → `1.30x` (stronger penalty)
5. Click "Update Pricing" on each tier
6. ✅ Overages now cost $0.079/min with 30% surge multiplier
7. Result: Heavy users will upgrade instead of paying overages

### Scenario 3: Adjust for Market Demand

If voice calling becomes more popular:

1. **Increase all monthly prices** by 10-20%:
   - Light: $39 → $45
   - Standard: $79 → $89
   - Professional: $129 → $149
   - Unlimited: $199 → $229

2. **Keep overage rate same** ($0.049) to avoid shock

3. Click "Update Pricing" on each tier

4. ✅ New revenue per tier increases ~15%

---

## Understanding the Dashboard

### Pricing Card Layout

Each card shows:

```
┌─────────────────────────────────────┐
│ Voice Light                         │
├─────────────────────────────────────┤
│ Monthly Price: $39                  │
│ Overage Per Min: $0.049             │
│ Minutes Included: 100               │
│ Surge Threshold: 50%                │
│ Surge Multiplier: 1.15x             │
├─────────────────────────────────────┤
│ ECONOMICS:                          │
│ Cost Basis:        $1.80/mo         │
│ Markup Ratio:      21.7x ✓          │
│ Profit Margin:     95%              │
├─────────────────────────────────────┤
│ [Update Pricing]                    │
└─────────────────────────────────────┘
```

**Key Metrics:**
- **Cost Basis**: Actual API cost ($0.018/min × minutes used)
- **Markup Ratio**: Revenue ÷ Cost (target: >9x for profitable pricing)
- **Profit Margin**: (Revenue - Cost) / Revenue (target: >70%)

### Health Indicators

✓ **Green (Excellent):** Markup >12x, Margin >85%
✓ **Blue (Good):** Markup 9x-12x, Margin 70-85%
⚠️ **Yellow (Warning):** Markup <9x, Margin <70%
❌ **Red (Danger):** Negative profit

---

## Audit Trail

Every pricing change is logged:

```
Event: admin_voice_pricing_change
Message: Voice tier "standard" updated: price=79.0
         overage=0.049 mins=300 by admin@company.com
Timestamp: 2026-03-30 14:05:23 UTC
```

All admins can see pricing change history in activity logs.

---

## Safety Guardrails

### Validation Rules (Automatically Enforced)

❌ **Cannot set negative prices:** Prices must be ≥ $0

❌ **Cannot set negative overage:** Overage must be ≥ $0

❌ **Surge threshold must be 0-100%:** Range enforced

❌ **Surge multiplier must be ≥ 1.0:** At least cost

### Admin Alerts

When you update pricing, you receive alerts:
- Email alert sent to admin@company.com
- Activity log entry created
- Notification in admin dashboard

---

## API Access

### Get Current Pricing (JSON)

```bash
curl -H "Cookie: session=<admin_token>" \
  https://yourapp.com/api/admin/voice-pricing
```

Response:
```json
{
  "voice_tiers": [
    {
      "tier": "light",
      "display_name": "Voice Light",
      "monthly_price_usd": 39.0,
      "minutes_included": 100,
      "overage_per_minute_usd": 0.049,
      "surge_threshold": 0.5,
      "surge_multiplier": 1.15,
      "cost_basis_usd": 1.80,
      "markup_ratio": 21.7,
      "is_active": true,
      "updated_at": "2026-03-30T14:05:23+00:00"
    },
    ...
  ]
}
```

---

## Recommended Pricing Strategy

### Current Defaults (Recommended to Keep)

```
LIGHT      $39/mo   (100 mins)     → 21.7x markup ⭐ (Excellent)
STANDARD   $79/mo   (300 mins)     → 14.6x markup ⭐ (Excellent)
PROF       $129/mo  (750 mins)     → 9.6x markup ⭐ (Very Good)
UNLIMITED  $199/mo  (unlimited)    → 5.5x markup ✓ (Acceptable)
```

### When to Adjust

**Increase prices if:**
- ✓ Demand for voice is high
- ✓ Competition is non-existent
- ✓ Profit margins trending down
- ✓ Usage per customer is high

**Decrease prices if:**
- ⚠️ Very few customers purchasing voice
- ⚠️ High churn after seeing price
- ⚠️ Major competitor launched cheaper option
- ⚠️ (Generally NOT recommended — focus on upsell instead)

**Increase overage rates if:**
- ✓ Many customers exceeding limits without upgrading
- ✓ Want to discourage unpredictable usage
- ✓ Want to encourage tier upgrades

---

## Troubleshooting

### "Updated successfully" but change not reflected

1. **Clear browser cache** (Ctrl+Shift+Delete)
2. **Refresh page** (Ctrl+R)
3. **Check audit log** to verify change was saved
4. **API endpoint** (`/api/admin/voice-pricing`) always has live data

### Can't access voice pricing panel

1. Check if you have **admin flag** set (contact owner)
2. Verify you're **logged in** (check session cookie)
3. Confirm you're visiting **/admin/voice-pricing** (not /pricing)

### Margin shows <70%

1. **This is a warning** — not ideal but operational
2. **If Unlimited tier:** expected, margins lower for unlimited
3. **If other tiers:** increase price or check cost_basis calculation
4. **Contact support** if margin seems wrong

---

## Best Practices

### ✅ DO

- Review pricing **monthly** against actual costs
- Adjust **one parameter at a time** to measure impact
- Watch **adoption rates** when changing prices
- Keep **overage rates high** to encourage upgrades
- Use **surge pricing** to prevent abuse

### ❌ DON'T

- Drop prices below **cost_basis** (you'll lose money)
- Change pricing **too frequently** (confuses customers)
- Set **overage rate to $0** (removes friction)
- Set **surge_multiplier < 1.0** (you're subsidizing overages)
- Forget to **audit log changes** (do this via admin panel)

---

## Example Pricing Adjustment Workflow

**Goal:** Increase voice revenue by 20% while maintaining 70%+ margins

**Step 1:** Go to `/admin/voice-pricing`

**Step 2:** Calculate new prices (target +15-20%):
```
Light:      $39 × 1.15 = $45
Standard:   $79 × 1.15 = $91
Professional: $129 × 1.15 = $148
Unlimited:  $199 × 1.15 = $229
```

**Step 3:** Update each tier with new prices

**Step 4:** Keep overage at $0.049/min (no change needed)

**Step 5:** Monitor for 1 week:
- Track conversion rate (how many add voice)
- Track churn rate (do customers cancel)
- Check margins (should still be >70%)

**Step 6:** If conversion stays healthy → **Change is good!**
If churn spikes → **Roll back prices**

---

## Summary

✅ **Complete control** over voice pricing without code changes
✅ **Real-time updates** — new customers see changes immediately
✅ **Margin tracking** — know your profitability per tier
✅ **Audit trail** — see who changed what and when
✅ **Safety guardrails** — can't accidentally set negative prices
✅ **API access** — programmatic pricing retrieval

The admin panel makes it easy to optimize pricing based on market conditions!

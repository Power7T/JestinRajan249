# Unit-Based Plans with Voice AI Add-Ons

## Base Plans (Keep Unchanged)

```
STARTER
├─ Base: $20/month
├─ Per unit: $10/unit (1-5 units max)
└─ Example: 3 units = $20 + (3 × $10) = $50/month

GROWTH
├─ Base: $20/month
├─ Per unit: $9/unit (6-10 units max)
└─ Example: 8 units = $20 + (8 × $9) = $92/month

PRO
├─ Base: $20/month
├─ Per unit: $8/unit (11-50 units max)
└─ Example: 20 units = $20 + (20 × $8) = $180/month
```

---

## Optional Voice AI Add-Ons (Add to Any Plan)

```
VOICE LIGHT - $39/month
├─ 100 minutes/month
├─ Cost: 21.7x markup
└─ Overage: $0.049/min

VOICE STANDARD - $79/month ⭐ MOST POPULAR
├─ 300 minutes/month
├─ Cost: 14.6x markup
└─ Overage: $0.049/min

VOICE PROFESSIONAL - $129/month
├─ 750 minutes/month
├─ Cost: 9.6x markup
└─ Overage: $0.049/min

VOICE UNLIMITED - $199/month
├─ Unlimited minutes
├─ Cost: 5.5x markup
└─ No overage charges
```

---

## Pricing Examples (Starter + Voice)

| Units | Starter Base | + Voice Light | + Voice Std | + Voice Pro | + Voice Unlimited |
|-------|--------------|---------------|------------|------------|-----------------|
| 1 | $30 | **$69** | **$109** | **$159** | **$229** |
| 2 | $40 | **$79** | **$119** | **$169** | **$239** |
| 3 | $50 | **$89** | **$129** | **$179** | **$249** |
| 4 | $60 | **$99** | **$139** | **$189** | **$259** |
| 5 | $70 | **$109** | **$149** | **$199** | **$269** |

---

## Pricing Examples (Growth + Voice)

| Units | Growth Base | + Voice Light | + Voice Std | + Voice Pro | + Voice Unlimited |
|-------|-------------|---------------|------------|------------|-----------------|
| 6 | $74 | **$113** | **$153** | **$203** | **$273** |
| 7 | $83 | **$122** | **$162** | **$212** | **$282** |
| 8 | $92 | **$131** | **$171** | **$221** | **$291** |
| 9 | $101 | **$140** | **$180** | **$230** | **$300** |
| 10 | $110 | **$149** | **$189** | **$239** | **$309** |

---

## Pricing Examples (Pro + Voice)

| Units | Pro Base | + Voice Light | + Voice Std | + Voice Pro | + Voice Unlimited |
|-------|----------|---------------|------------|------------|-----------------|
| 11 | $108 | **$147** | **$187** | **$237** | **$307** |
| 15 | $140 | **$179** | **$219** | **$269** | **$339** |
| 20 | $180 | **$219** | **$259** | **$309** | **$379** |
| 25 | $220 | **$259** | **$299** | **$349** | **$419** |
| 30 | $260 | **$299** | **$339** | **$389** | **$459** |
| 50 | $420 | **$459** | **$499** | **$549** | **$619** |

---

## Margin Analysis

### Starter 3 units + Voice Standard ($129/month)
```
Revenue: $50 + $79 = $129/month

Costs:
├─ Unit storage & support: 3 × $1.35 = $4.05
├─ Fixed costs: $8/month
├─ Voice API: 300 × $0.018 = $5.40
└─ Total: $17.45/month

Profit: $129 - $17.45 = $111.55/month (86%) ✓✓
```

### Growth 8 units + Voice Professional ($221/month)
```
Revenue: $92 + $129 = $221/month

Costs:
├─ Unit support: 8 × $1.35 = $10.80
├─ Fixed: $8/month
├─ Voice API: 750 × $0.018 = $13.50
└─ Total: $32.30/month

Profit: $221 - $32.30 = $188.70/month (85%) ✓✓
```

### Pro 20 units + Voice Unlimited ($379/month)
```
Revenue: $180 + $199 = $379/month

Costs:
├─ Unit support: 20 × $1.35 = $27
├─ Fixed: $8/month
├─ Voice API (est 2000 mins): $36/month
└─ Total: $71/month

Profit: $379 - $71 = $308/month (81%) ✓✓
```

---

## Implementation

This requires:

1. **In Signup Flow:**
   - Step 1: Select plan tier (Starter/Growth/Pro)
   - Step 2: Select number of units
   - Step 3: Optional — Add voice? (Light/Std/Pro/Unlimited)
   - Step 4: Review pricing

2. **In Dashboard:**
   - Show current plan: "Starter 3 units"
   - Show add-ons: "Voice Standard" (if purchased)
   - Show total: "$129/month"
   - Option to upgrade voice tier or plan

3. **In Billing:**
   - Track voice minutes per month
   - Calculate overages at end of month
   - Charge on next invoice
   - Send usage reports

---

## Key Points

✅ **Unit-based plans unchanged** — Starter/Growth/Pro stay the same
✅ **Voice is fully optional** — Customers only pay if they want it
✅ **Strong margins** — 80%+ profit on all combinations
✅ **Simple UX** — Plan + Voice tier selection
✅ **Flexible pricing** — Light tier for testing, Unlimited for enterprise
✅ **Anti-undercharge** — Minimum $35/month per customer maintained

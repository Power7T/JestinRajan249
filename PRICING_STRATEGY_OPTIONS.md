# Pricing Strategy Options for New Plans

## Current Situation
- **Old Plans**: Channel-based (Baileys $19, Meta $29, SMS $19, Pro $49)
- **New Structure**: Unit-based (Starter $20+$10/unit, Growth $20+$9/unit, Pro $20+$8/unit)
- **New Feature**: Voice AI Calling (expensive due to Twilio + Deepgram + OpenAI + ElevenLabs)

---

## STRATEGY 1: Feature-Tier with Voice Add-On ⭐ RECOMMENDED

Keep base pricing simple, add voice as separate premium tier.

```
STARTER (Essential) — 1-5 properties
├─ Base: $25/mo + $10/unit
├─ Features: Email/SMS/iCal, team members (2), guest contacts
├─ Voice: No (optional add-on)
└─ Example: 3 units = $55/mo

GROWTH (Professional) — 6-15 properties
├─ Base: $45/mo + $8/unit
├─ Features: Everything in Starter + WhatsApp, advanced analytics, audit logs
├─ Voice: No (optional add-on)
└─ Example: 8 units = $109/mo

PRO (Enterprise) — 16-50+ properties
├─ Base: $75/mo + $6/unit
├─ Features: Everything in Growth + admin dashboard, priority support
├─ Voice: No (optional add-on)
└─ Example: 20 units = $195/mo

VOICE AI ADD-ON (For any tier)
├─ $59/mo: 500 minutes of voice calls
├─ $129/mo: 2,000 minutes (unlimited for most hosts)
├─ Pay-as-you-go: $0.029/minute for overages
└─ Includes: Deepgram STT, OpenAI GPT-4o, ElevenLabs TTS
```

**Pros:** Clear separation, hosts choose if they want voice, predictable costs
**Cons:** More SKUs to manage, some hosts might not understand value

---

## STRATEGY 2: Voice Included in Higher Tiers

Include voice only in top tiers to encourage upgrades.

```
STARTER (DIY) — 1-5 properties
├─ Base: $20/mo + $10/unit
├─ Features: Email/SMS/iCal, team members (2)
├─ Voice: No
└─ Example: 3 units = $50/mo

GROWTH (Smart) — 6-15 properties
├─ Base: $45/mo + $8/unit
├─ Features: Starter + WhatsApp, guest contacts, voice (300 mins/mo)
├─ Voice: Included 300 mins/mo ($0.029/min overage)
└─ Example: 8 units = $109/mo

PRO (Unlimited) — 16-50+ properties
├─ Base: $85/mo + $6/unit
├─ Features: Growth + admin dashboard, voice unlimited, analytics, priority support
├─ Voice: Unlimited (all calls included)
└─ Example: 20 units = $205/mo
```

**Pros:** Encourages tier migration, bundled value, simpler messaging
**Cons:** Some Starter/Growth users need voice but have to upgrade

---

## STRATEGY 3: All-In-One Pricing (Aggressive Growth)

Include everything in all tiers, different inclusion levels.

```
STARTER (All-In) — 1-5 properties
├─ Base: $35/mo + $12/unit
├─ Everything included: Email, SMS, WhatsApp, Voice (100 mins/mo)
├─ Voice: 100 mins/mo included, $0.029/min over
└─ Example: 3 units = $71/mo

GROWTH (Power User) — 6-15 properties
├─ Base: $59/mo + $9/unit
├─ Everything included: Voice (500 mins/mo), analytics, audit logs
├─ Voice: 500 mins/mo included, $0.029/min over
└─ Example: 8 units = $131/mo

PRO (Enterprise) — 16-50+ properties
├─ Base: $99/mo + $7/unit
├─ Everything included: Unlimited voice, admin dashboard, priority support
├─ Voice: Unlimited
└─ Example: 20 units = $239/mo
```

**Pros:** Simplest for users, no surprises, all features available
**Cons:** Higher entry price, voice costs eat margin for light users

---

## Cost Analysis at Scale

### Scenario: Host with 5 properties, 100 voice calls/month (50 mins)

**Strategy 1 (Voice Add-On):**
- Starter: $25 + (5 × $10) = $75/mo
- Voice 500 mins: $59/mo
- **Total: $134/mo** (our margin: ~$100/mo after voice API costs)

**Strategy 2 (Voice in Pro):**
- Pro: $85 + (5 × $6) = $115/mo
- Voice unlimited: included
- **Total: $115/mo** (our margin: ~$75/mo)

**Strategy 3 (All-In):**
- Growth: $59 + (5 × $9) = $104/mo
- Voice included (50 mins < 500 limit)
- **Total: $104/mo** (our margin: ~$85/mo)

---

## Recommendation

**STRATEGY 1** (Voice Add-On) is best because:
1. ✅ Maximizes margin for hosts not using voice
2. ✅ Hosts who need voice pay for it (fair pricing)
3. ✅ Simple tier progression
4. ✅ Clear cost attribution
5. ✅ Easiest to implement in billing system

**Suggested Implementation:**
- Update billing.py with new PLAN_INFO
- Add voice SKUs to Stripe
- Update signup flow to show optional voice add-on
- Track voice usage and alert at 80% of limit
- Offer automatic overage protection at checkout

---

## What to Implement

If you choose Strategy 1, I'll update:

1. **web/billing.py** - Add pricing structure
2. **Database** - Update PlanConfig with new rates
3. **Signup flow** - Show voice add-on option
4. **Pricing page** - Display new tiers
5. **Stripe products** - Create new price IDs

Which strategy do you prefer, or should I propose something different?

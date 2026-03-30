# Voice AI System - Complete Phase Roadmap

## ✅ COMPLETED PHASES (1-4)

### Phase 1: Voice Calling Infrastructure
- ✅ Twilio integration (incoming/outbound calls)
- ✅ Deepgram STT (speech-to-text)
- ✅ OpenAI GPT-4o-mini (LLM responses)
- ✅ ElevenLabs TTS (text-to-speech)
- ✅ VoiceCall model with full tracking
- ✅ Rate limiting & idempotency checks
- ✅ Call recording & transcription

### Phase 2: Guest Data Integration
- ✅ Multi-source guest identification (phone → name → confirmation)
- ✅ Reservation linking (CSV/PMS/iCal)
- ✅ Room/property context in AI responses
- ✅ Fallback lookup chains
- ✅ Auto-linking GuestContact ↔ Reservation
- ✅ iCal event → Reservation bridge

### Phase 3: Admin Pricing Management
- ✅ Voice pricing tiers (Light/Standard/Pro/Unlimited)
- ✅ Cost-based markup calculations (5.5x - 21.7x)
- ✅ Real-time margin tracking
- ✅ Admin panel for dynamic pricing
- ✅ Audit trail for price changes

### Phase 4: Knowledge Gap Handling
- ✅ VoiceKnowledgeGap model for unanswered questions
- ✅ Auto-creation of support tickets
- ✅ Host notification system
- ✅ Gap resolution workflow
- ✅ Integration with activity logs

---

## 📋 REMAINING PHASES (5-12)

### Phase 5: Voice Analytics & Reporting
**Status:** Partially implemented (data tracked, UI missing)
**Priority:** 🔴 CRITICAL
**Business Impact:** ⭐⭐⭐⭐⭐

**What's needed:**
- Dashboard: Call volume, avg duration, sentiment trends
- Per-guest analytics: repeat call patterns, common issues
- Per-property analytics: peak call times, resolution rates
- Cost analysis: actual cost vs. pricing, margin tracking per tier
- Reporting exports: CSV/PDF for stakeholders
- Real-time call metrics

**Key Metrics to Track:**
- Total calls (daily/weekly/monthly)
- Average call duration
- Call completion rate (% answered vs abandoned)
- Sentiment distribution (positive/neutral/negative)
- Knowledge gaps per property (top issues)
- Cost per call vs. tier revenue
- Guest satisfaction score trends
- Repeat caller percentage

**Files to create:**
- `web/templates/admin_voice_analytics.html` - dashboard UI
- `web/voice_analytics.py` - analytics calculation engine

**Files to update:**
- `web/app.py` - add endpoint: `GET /admin/voice-analytics`
- `web/models.py` - add analytics aggregate functions

**Estimated effort:** 2-3 hours
**Dependencies:** None

---

### Phase 6: Call Recordings & Transcripts Archive
**Status:** Partially implemented (recording_url tracked, no archive/search)
**Priority:** 🔴 HIGH
**Business Impact:** ⭐⭐⭐⭐

**What's needed:**
- Recording storage management (auto-expire old recordings per GDPR)
- Searchable transcript archive with timestamps
- Full conversation replay UI (audio + transcript sync)
- Transcript download (PDF/docx for guest/host)
- Redaction tool for sensitive info (PII removal)
- Compliance dashboard (GDPR/CCPA consent tracking)
- Transcript search: full-text search across all calls
- Call summary extraction (auto-generated brief)

**Key Features:**
- Play audio with synchronized transcript
- Jump to specific time in transcript
- Export conversation as PDF with branding
- Redact sensitive info before sharing
- Track who has accessed recording
- Auto-delete recordings after X days (configurable)

**Files to create:**
- `web/templates/admin_call_recordings.html` - recording archive UI
- `web/transcript_processor.py` - transcript extraction/search

**Files to update:**
- `web/app.py` endpoints:
  - `GET /admin/recordings` - search + filter
  - `GET /admin/recordings/{call_id}` - playback + transcript
  - `POST /admin/recordings/{call_id}/download`
  - `POST /admin/recordings/{call_id}/redact`
- `web/models.py` - add transcript_text, redactions fields
- New migration: add transcript storage

**Estimated effort:** 4-5 hours
**Dependencies:** Phase 5 (for call search)

---

### Phase 7: Smart Routing & Escalation
**Status:** Not implemented
**Priority:** 🔴 CRITICAL
**Business Impact:** ⭐⭐⭐⭐⭐

**What's needed:**
- Call routing rules: direct to host, voicemail, queue, transfer
- Escalation triggers: sentiment degradation, repeat calls, keywords
- Host availability calendar: only route if available
- Fallback SMS: if call fails, auto-send info via SMS/WhatsApp
- Auto-rejection for spam/blocked numbers
- Dead air detection: hang up if guest silent >30s
- Queue management: hold music, position in queue
- Call transfer: seamlessly handoff to team member
- Priority routing: VIP guests → immediate human

**Routing Rules Engine:**
```
IF guest_sentiment = negative AND call_count > 2
  THEN escalate_to_host(urgent=true)

IF time = check_in_day AND issue_type = access
  THEN immediate_escalation

IF host_available = false
  THEN voice_mail + fallback_sms
```

**Files to create:**
- `web/voice_routing.py` - routing logic engine
- `web/templates/admin_voice_routing.html` - config UI
- New migration: `20260331_0100_add_voice_routing_config.py`

**Files to update:**
- `web/models.py` - VoiceRoutingConfig, RoutingRule models
- `web/app.py` - integrate routing into handle_incoming_call
- `web/integrations/voice.py` - add escalation triggers

**Estimated effort:** 5-6 hours
**Dependencies:** Phase 5 (for sentiment data)

---

### Phase 8: Scheduled Callbacks & Reminders
**Status:** Partially implemented (callback_requested tracked, no scheduling)
**Priority:** 🟠 HIGH
**Business Impact:** ⭐⭐⭐⭐

**What's needed:**
- Callback scheduler: queue guest callbacks at requested time
- SMS/WhatsApp reminder: 15min before scheduled callback
- Auto-dial guest at scheduled time
- Callback success tracking
- Reschedule UI: guest can reschedule via SMS link
- Host override: admin can manually trigger callback
- Callback history dashboard
- Timezone-aware scheduling

**Callback Workflow:**
```
Guest requests callback
  → Extract time from message
  → Confirm via SMS: "Callback scheduled for 3 PM tomorrow?"
  → Guest confirms/reschedules
  → At callback time: auto-dial guest
  → If no answer: mark failed, offer reschedule
```

**Files to create:**
- `web/callback_worker.py` - background job scheduler
- `web/templates/admin_callbacks.html` - queue + history

**Files to update:**
- `web/models.py` - CallbackSchedule model
- `web/app.py` - callback endpoints + schedule confirmation
- `web/db.py` - add callback job to background worker

**Estimated effort:** 3-4 hours
**Dependencies:** None (uses existing SMS infrastructure)

---

### Phase 9: Voice Performance Optimization
**Status:** Partially implemented (duration_seconds tracked, no optimization)
**Priority:** 🟠 MEDIUM
**Business Impact:** ⭐⭐⭐

**What's needed:**
- SLA metrics: avg response time, answer rate %, abandonment rate
- Latency optimization: reduce STT→LLM→TTS round trip
- Quality scoring: voice clarity, background noise detection
- Cost optimization: suggest tier downgrades for light users
- Performance alerts: notify admin if call quality drops
- Concurrent call limits: prevent system overload
- Call queue monitoring: prevent dropped calls
- A/B testing: test different AI prompts for conversion

**Performance KPIs:**
- Average first response time: <3 seconds
- Call completion rate: >85%
- Average call duration: 2-5 minutes
- AI response accuracy: >90%
- Cost per call: $0.15-$0.25
- Quality score: 8+/10

**Files to create:**
- `web/voice_performance.py` - metrics calculation
- `web/templates/admin_voice_sla.html` - SLA dashboard

**Files to update:**
- `web/models.py` - add quality_score, latency_ms fields
- `web/app.py` - SLA calculation + alerts
- `web/integrations/voice.py` - add quality monitoring

**Estimated effort:** 3-4 hours
**Dependencies:** Phase 5 (for metrics)

---

### Phase 10: Multi-Language & International Support
**Status:** Partially implemented (language detection exists, limited)
**Priority:** 🟡 MEDIUM
**Business Impact:** ⭐⭐⭐

**What's needed:**
- Improved language auto-detection (use langdetect lib)
- Multi-language AI responses (already in prompt, improve)
- International phone number validation (current: US-centric)
- Timezone-aware scheduling (callbacks respect guest timezone)
- Currency auto-conversion (show pricing in guest's currency)
- Regional compliance: GDPR (EU), CCPA (CA), etc.
- Native speaker quality: select region-specific TTS voices
- Localized prompts: adapt to cultural norms

**Supported Languages:**
- English (US, UK, AU, CA)
- Spanish (ES, MX, AR)
- French (FR, CA)
- German (DE, AT)
- Italian (IT)
- Portuguese (BR, PT)
- Mandarin (China, Taiwan)
- Japanese (JP)

**Files to update:**
- `web/integrations/voice.py` - enhance language detection
- `web/phone_utils.py` - expand international phone validation
- `web/app.py` - add timezone logic to callbacks
- `web/models.py` - add user_language, user_timezone fields
- `web/templates/` - add language + currency selector

**Estimated effort:** 2-3 hours
**Dependencies:** None

---

### Phase 11: Team Collaboration & Call Coaching
**Status:** Not implemented
**Priority:** 🟡 MEDIUM
**Business Impact:** ⭐⭐⭐

**What's needed:**
- Call coaching dashboard: review calls, provide feedback
- Coaching notes: attach to transcript for team
- Performance reviews: track individual call quality
- Best practices library: share top-performing calls
- A/B testing prompts: test different AI behaviors
- Call reassignment: switch to different host/team member
- Team leaderboard: top performers (calls answered, ratings)
- Quality score contribution: what makes a call excellent

**Coaching Workflow:**
```
Admin listens to call
  → Pauses at key moments
  → Adds coaching notes
  → Tags best practices (empathy, clarity, knowledge)
  → Assigns to team member's learning queue
  → Team member reviews + confirms understanding
```

**Files to create:**
- `web/templates/admin_call_coaching.html`
- `web/call_coaching.py` - logic

**Files to update:**
- `web/models.py` - CallCoachingNote, CallFeedback models
- `web/app.py` - coaching endpoints

**Estimated effort:** 4-5 hours
**Dependencies:** Phase 6 (for transcript playback)

---

### Phase 12: Integration with Issue Ticketing
**Status:** Partially implemented (knowledge gaps → tickets)
**Priority:** 🟡 LOW
**Business Impact:** ⭐⭐

**What's needed:**
- Link voice calls to existing issue tickets (issues.html)
- Auto-create tickets from voice calls + sentiment
- Voice call context in ticket details (transcript snippet)
- Ticket resolution from voice call
- Callback when ticket is resolved
- Voice call as proof of issue (transcript + recording)
- Two-way sync: ticket updates mentioned in follow-up calls
- Analytics: ticket resolution rate from voice calls

**Integration Points:**
- VoiceCall → IssueTicket (many-to-one)
- Auto-create ticket when sentiment = negative
- Show related calls in ticket view
- Link resolved ticket back to guest

**Files to update:**
- `web/models.py` - add voice_call_id FK to IssueTicket
- `web/app.py` - enhance _create_voice_ticket() function
- `web/templates/issues.html` - add voice call section
- `web/templates/issue_detail.html` - show related calls

**Estimated effort:** 2-3 hours
**Dependencies:** None

---

## 🎯 RECOMMENDED IMPLEMENTATION ORDER

### Week 1 - Foundation & Insights
1. **Phase 5: Voice Analytics** (2-3h) - Business intelligence
2. **Phase 7: Smart Routing** (5-6h) - Guest experience
3. Total: 7-9 hours

### Week 2 - Operations & Guest Experience
4. **Phase 6: Call Recordings** (4-5h) - Compliance + quality
5. **Phase 8: Scheduled Callbacks** (3-4h) - Guest satisfaction
6. Total: 7-9 hours

### Week 3 - Optimization & Scale
7. **Phase 9: Performance Optimization** (3-4h) - Cost reduction
8. **Phase 10: Multi-Language** (2-3h) - Market expansion
9. Total: 5-7 hours

### Week 4 - Team & Integration
10. **Phase 11: Call Coaching** (4-5h) - Team development
11. **Phase 12: Ticket Integration** (2-3h) - Customer success
12. Total: 6-8 hours

**Grand Total: ~25-33 hours over 4 weeks**

---

## 📊 FEATURE PRIORITY MATRIX

| Phase | Complexity | Business Impact | User Type | Time | Dependencies |
|-------|-----------|-----------------|-----------|------|--------------|
| 5 | Medium | ⭐⭐⭐⭐⭐ | Admin | 2-3h | None |
| 6 | Medium | ⭐⭐⭐⭐ | Admin+Guest | 4-5h | Phase 5 |
| 7 | High | ⭐⭐⭐⭐⭐ | Host+Guest | 5-6h | Phase 5 |
| 8 | Medium | ⭐⭐⭐⭐ | Guest+Admin | 3-4h | None |
| 9 | Medium | ⭐⭐⭐ | Admin | 3-4h | Phase 5 |
| 10 | Low | ⭐⭐⭐ | All | 2-3h | None |
| 11 | High | ⭐⭐⭐ | Team | 4-5h | Phase 6 |
| 12 | Low | ⭐⭐ | Admin | 2-3h | None |

---

## 🚀 Quick Start: Phase 5 (Voice Analytics)

**Recommended first phase** because:
- High ROI: immediate business insights
- No external dependencies
- Uses existing VoiceCall data (already tracked)
- 2-3 hours of work
- Enables Phases 6, 7, 9

**Core metrics to build first:**
1. Call volume dashboard (last 7/30/90 days)
2. Average call duration by property
3. Sentiment distribution pie chart
4. Top knowledge gaps (unanswered questions)
5. Cost per call analysis by tier
6. Guest satisfaction trend

---

## 🔄 Parallel Implementation Path

**Can be started simultaneously:**
- Phase 5 + Phase 7 + Phase 8 (no cross-dependencies)
- Phase 10 (standalone, no dependencies)
- Phase 12 (uses existing ticket system)

**Recommended parallel approach:**
- **Developer 1:** Phase 5 (analytics)
- **Developer 2:** Phase 7 (routing)
- **Parallel:** Phase 8 (callbacks)
- **Time saved:** ~4-5 hours vs sequential

---

## ✨ Future Enhancements (Post-Phase 12)

**Stretch goals:**
- ML-based issue prediction (detect problems before guest mentions)
- Automatic guest segmentation (VIP vs standard routing)
- Chatbot-to-human handoff (pre-warm human with context)
- Voice biometric verification (guest identification by voice)
- Emotion-driven escalation (detect frustration, auto-escalate)
- Real-time language translation (guest + host in different languages)
